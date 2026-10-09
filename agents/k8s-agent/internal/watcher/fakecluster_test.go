package watcher

// R46: the watcher against client-go's fake clientset (real informers, real API types) and a
// recording ingest server. Proves discovery, start/success/fail, failure reasons, one terminal event
// per Job across updates and resyncs, and per-Job state cleanup on deletion.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/fake"
)

type ingest struct {
	mu     sync.Mutex
	events []ExecEvent
	specs  []CronJobSpec
}

func (in *ingest) server() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		in.mu.Lock()
		defer in.mu.Unlock()
		switch r.URL.Path {
		case "/agent/v1/k8s/events":
			var body struct{ Events []ExecEvent }
			json.Unmarshal(b, &body)
			in.events = append(in.events, body.Events...)
		case "/agent/v1/k8s/cronjobs":
			var body struct{ Cronjobs []CronJobSpec }
			json.Unmarshal(b, &body)
			in.specs = body.Cronjobs
		}
		w.Write([]byte(`{}`))
	}))
}

func (in *ingest) count(kind, exec string) int {
	in.mu.Lock()
	defer in.mu.Unlock()
	n := 0
	for _, e := range in.events {
		if e.Kind == kind && e.ExecutionID == exec {
			n++
		}
	}
	return n
}

func (in *ingest) find(kind, exec string) *ExecEvent {
	in.mu.Lock()
	defer in.mu.Unlock()
	for i := range in.events {
		if in.events[i].Kind == kind && in.events[i].ExecutionID == exec {
			return &in.events[i]
		}
	}
	return nil
}

func eventually(t *testing.T, what string, f func() bool) {
	t.Helper()
	for i := 0; i < 100; i++ {
		if f() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for: %s", what)
}

func job(name, uid string, cron *batchv1.CronJob) *batchv1.Job {
	start := metav1.NewTime(time.Now().Add(-2 * time.Minute))
	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "prod", UID: types.UID(uid),
			OwnerReferences: []metav1.OwnerReference{{Kind: "CronJob", Name: cron.Name, UID: cron.UID, APIVersion: "batch/v1"}}},
		Status: batchv1.JobStatus{StartTime: &start},
	}
}

func TestWatcherAgainstFakeCluster(t *testing.T) {
	in := &ingest{}
	srv := in.server()
	defer srv.Close()

	cron := &batchv1.CronJob{ObjectMeta: metav1.ObjectMeta{Name: "nightly-backup", Namespace: "prod", UID: "cj-1"},
		Spec: batchv1.CronJobSpec{Schedule: "0 2 * * *", ConcurrencyPolicy: batchv1.ForbidConcurrent}}
	other := &batchv1.CronJob{ObjectMeta: metav1.ObjectMeta{Name: "ignored", Namespace: "kube-system", UID: "cj-2"},
		Spec: batchv1.CronJobSpec{Schedule: "* * * * *"}}
	ok := job("nightly-backup-1", "job-ok", cron)
	done := metav1.NewTime(time.Now())
	ok.Status.Succeeded, ok.Status.CompletionTime = 1, &done
	bad := job("nightly-backup-2", "job-bad", cron)
	bad.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobFailed, Status: corev1.ConditionTrue, Reason: "BackoffLimitExceeded",
		Message: "Job has reached the specified backoff limit", LastTransitionTime: done}}

	cs := fake.NewSimpleClientset(cron, other, ok, bad)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	w := New(cs, NewClient(srv.URL, "agent", "key", false), []string{"prod"})
	go w.Run(ctx, 100*time.Millisecond, 100*time.Millisecond)

	// discovery: only the in-scope CronJob, with its schedule and policy
	eventually(t, "cronjob discovery", func() bool { in.mu.Lock(); defer in.mu.Unlock(); return len(in.specs) == 1 })
	in.mu.Lock()
	if s := in.specs[0]; s.Name != "nightly-backup" || s.Schedule != "0 2 * * *" || s.ConcurrencyPolicy != "Forbid" {
		t.Fatalf("spec %+v", s)
	}
	in.mu.Unlock()

	// execution events
	eventually(t, "success of job-ok", func() bool { return in.count("success", "k8s-job-ok") == 1 })
	eventually(t, "fail of job-bad", func() bool { return in.count("fail", "k8s-job-bad") == 1 })
	if in.count("start", "k8s-job-ok") != 1 {
		t.Fatal("expected one start event for job-ok")
	}
	if f := in.find("fail", "k8s-job-bad"); f == nil || f.Reason == "" {
		t.Fatalf("fail event should carry the Kubernetes reason, got %+v", f)
	}

	// R39 regression: further updates of a finished Job must not re-send its terminal event
	for i := 0; i < 3; i++ {
		ok.Labels = map[string]string{"touch": string(rune('a' + i))}
		if _, err := cs.BatchV1().Jobs("prod").Update(ctx, ok, metav1.UpdateOptions{}); err != nil {
			t.Fatal(err)
		}
	}
	time.Sleep(500 * time.Millisecond)
	if n := in.count("success", "k8s-job-ok"); n != 1 {
		t.Fatalf("success re-sent on update: %d", n)
	}

	// R39: deleting the Job frees its per-Job state
	if err := cs.BatchV1().Jobs("prod").Delete(ctx, "nightly-backup-1", metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	eventually(t, "state cleanup on delete", func() bool {
		w.mu.Lock()
		defer w.mu.Unlock()
		_, a := w.jobToCron["job-ok"]
		return !a && !w.started["job-ok"] && !w.finished["job-ok"]
	})
}

// R65: time zone and jobwatch.io annotations are reported; other annotations never leave the cluster.
func TestCronJobSpecTimeZoneAndAnnotations(t *testing.T) {
	tz := "Asia/Kolkata"
	cj := &batchv1.CronJob{}
	cj.Name, cj.Namespace = "report", "billing"
	cj.Spec.Schedule = "0 2 * * *"
	cj.Spec.TimeZone = &tz
	cj.Annotations = map[string]string{
		"jobwatch.io/grace": "10m", "jobwatch.io/tags": "db,prod",
		"kubectl.kubernetes.io/last-applied-configuration": `{"env":[{"name":"DB_PASSWORD","value":"s3cret"}]}`,
		"example.com/token": "abc",
	}
	s := cronJobSpec(cj)
	if s.TimeZone != "Asia/Kolkata" {
		t.Fatalf("time zone not reported: %q", s.TimeZone)
	}
	if len(s.Annotations) != 2 || s.Annotations["jobwatch.io/grace"] != "10m" {
		t.Fatalf("expected only the two jobwatch.io annotations, got %v", s.Annotations)
	}
	none := &batchv1.CronJob{}
	if cronJobSpec(none).Annotations != nil {
		t.Fatal("no annotations should be reported as nil (omitted)")
	}
}
