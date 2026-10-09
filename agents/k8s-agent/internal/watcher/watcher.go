// Package watcher uses client-go informers on CronJobs, Jobs, Pods and Events and translates them into
// WeCrew JobWatch discovery + execution events. Read-only; RBAC in the Helm chart grants get/list/watch only.
package watcher

import (
	"context"
	"encoding/json"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"
)

// Failure reasons we detect explicitly (spec §8).
var podFailureReasons = map[string]bool{"ImagePullBackOff": true, "ErrImagePull": true, "CrashLoopBackOff": true, "OOMKilled": true, "Evicted": true, "DeadlineExceeded": true, "CreateContainerConfigError": true}

type CronJobSpec struct {
	Namespace, Name, Schedule, ConcurrencyPolicy, Image, Command string
	Suspend                                                      bool
	SuccessfulHistoryLimit, FailedHistoryLimit                   *int32
	ActiveDeadlineS, StartingDeadlineS                           *int64
	LastScheduleAt, LastSuccessAt                                *time.Time
	UID                                                          string
	// R65: spec.timeZone (schedules were treated as UTC before), and only the jobwatch.io/* annotations
	// (other annotations can carry anything, including secrets, and are never sent).
	TimeZone    string            `json:",omitempty"`
	Annotations map[string]string `json:",omitempty"`
}

const annotationPrefix = "jobwatch.io/"

// jobwatchAnnotations keeps at most 20 jobwatch.io/* annotations, values capped at 1,000 bytes.
func jobwatchAnnotations(in map[string]string) map[string]string {
	var out map[string]string
	for k, v := range in {
		if !strings.HasPrefix(k, annotationPrefix) || len(out) >= 20 {
			continue
		}
		if out == nil {
			out = map[string]string{}
		}
		if len(v) > 1000 {
			v = v[:1000]
		}
		out[k] = v
	}
	return out
}

type ExecEvent struct {
	Kind        string    `json:"kind"` // start|success|fail
	CronJobUID  string    `json:"cronjob_uid"`
	Namespace   string    `json:"namespace"`
	CronJob     string    `json:"cronjob"`
	JobName     string    `json:"job_name"`
	ExecutionID string    `json:"execution_id"`
	Sequence    int       `json:"sequence"`
	AgentTS     time.Time `json:"agent_ts"`
	DurationMS  *int64    `json:"duration_ms,omitempty"`
	ExitCode    *int      `json:"exit_code,omitempty"`
	// R39: was `Pod, Node string` with ONE tag "pod" on both — encoding/json drops both fields when
	// two share a name, so neither pod nor node ever reached the server (and go vet rejects it).
	Pod        string            `json:"pod,omitempty"`
	Node       string            `json:"node,omitempty"`
	Reason     string            `json:"reason,omitempty"`
	StderrTail string            `json:"stderr_tail,omitempty"`
	Meta       map[string]string `json:"meta,omitempty"`
}

type Watcher struct {
	cs        kubernetes.Interface
	client    *Client
	nsFilter  map[string]bool // empty = cluster scope
	mu        sync.Mutex
	pending   []ExecEvent
	jobToCron map[string]string // job uid -> cronjob uid
	started   map[string]bool
	finished  map[string]bool // R39: terminal event already emitted for this Job uid
	disk      DiskQueue       // R40: durable queue; nil → in-memory only
}

// DiskQueue is the slice of buffer.Buffer the watcher needs (an interface keeps the watcher
// testable without a filesystem).
type DiskQueue interface {
	Push(v any) error
	Peek(n int) ([]json.RawMessage, int64, error)
	Ack(offset int64) error
}

// WithDiskQueue makes events survive agent restarts and ingest outages.
func (w *Watcher) WithDiskQueue(q DiskQueue) *Watcher { w.disk = q; return w }

// maxPending bounds the in-memory re-queue while the ingest tier is unreachable (no disk buffer
// yet). Oldest events are dropped first; the server's slot reconciliation still records the runs
// as missed/unobserved rather than silently losing them.
const maxPending = 20000

func New(cs kubernetes.Interface, client *Client, namespaces []string) *Watcher {
	w := &Watcher{cs: cs, client: client, nsFilter: map[string]bool{}, jobToCron: map[string]string{}, started: map[string]bool{}, finished: map[string]bool{}}
	for _, n := range namespaces {
		if n != "" {
			w.nsFilter[n] = true
		}
	}
	return w
}

func (w *Watcher) inScope(ns string) bool { return len(w.nsFilter) == 0 || w.nsFilter[ns] }

func (w *Watcher) push(e ExecEvent) {
	if w.disk != nil {
		if err := w.disk.Push(e); err == nil {
			return
		} else {
			log.Printf("disk queue: %v (keeping event in memory)", err)
		}
	}
	w.mu.Lock()
	w.pending = append(w.pending, e)
	if over := len(w.pending) - maxPending; over > 0 {
		w.pending = w.pending[over:]
	}
	w.mu.Unlock()
}

// flushDisk sends the durable queue in batches; an event leaves the disk only after the server
// accepted it.
func (w *Watcher) flushDisk(ctx context.Context) {
	for ctx.Err() == nil {
		lines, next, err := w.disk.Peek(500)
		if err != nil {
			log.Printf("disk queue read: %v", err)
			return
		}
		if len(lines) == 0 {
			if next > 0 {
				_ = w.disk.Ack(next) // only skipped/corrupt lines were consumed
			}
			return
		}
		if err := w.client.Post(ctx, "/agent/v1/k8s/events", map[string]any{"events": lines}, nil); err != nil {
			log.Printf("flush: %v (%d events stay queued on disk)", err, len(lines))
			return
		}
		if err := w.disk.Ack(next); err != nil {
			log.Printf("disk queue ack: %v", err)
			return
		}
	}
}

func cronJobSpec(cj *batchv1.CronJob) CronJobSpec {
	s := CronJobSpec{Namespace: cj.Namespace, Name: cj.Name, Schedule: cj.Spec.Schedule, ConcurrencyPolicy: string(cj.Spec.ConcurrencyPolicy), UID: string(cj.UID),
		SuccessfulHistoryLimit: cj.Spec.SuccessfulJobsHistoryLimit, FailedHistoryLimit: cj.Spec.FailedJobsHistoryLimit, StartingDeadlineS: cj.Spec.StartingDeadlineSeconds,
		ActiveDeadlineS: cj.Spec.JobTemplate.Spec.ActiveDeadlineSeconds}
	if cj.Spec.Suspend != nil {
		s.Suspend = *cj.Spec.Suspend
	}
	if cj.Spec.TimeZone != nil {
		s.TimeZone = *cj.Spec.TimeZone
	}
	s.Annotations = jobwatchAnnotations(cj.Annotations)
	if cj.Status.LastScheduleTime != nil {
		t := cj.Status.LastScheduleTime.Time
		s.LastScheduleAt = &t
	}
	if cj.Status.LastSuccessfulTime != nil {
		t := cj.Status.LastSuccessfulTime.Time
		s.LastSuccessAt = &t
	}
	if cs := cj.Spec.JobTemplate.Spec.Template.Spec.Containers; len(cs) > 0 {
		s.Image = cs[0].Image
		s.Command = strings.Join(append(cs[0].Command, cs[0].Args...), " ")
	}
	return s
}

// Run starts informers and the flush loop; blocks until ctx is done.
func (w *Watcher) Run(ctx context.Context, discoveryEvery, flushEvery time.Duration) {
	f := informers.NewSharedInformerFactory(w.cs, 10*time.Minute)
	cjInf := f.Batch().V1().CronJobs().Informer()
	jobInf := f.Batch().V1().Jobs().Informer()
	podInf := f.Core().V1().Pods().Informer()
	evInf := f.Core().V1().Events().Informer()

	jobInf.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    func(o any) { w.onJob(o.(*batchv1.Job)) },
		UpdateFunc: func(_, o any) { w.onJob(o.(*batchv1.Job)) },
		DeleteFunc: func(o any) { w.forgetJob(o) },
	})
	podInf.AddEventHandler(cache.ResourceEventHandlerFuncs{
		UpdateFunc: func(old, o any) {
			// resyncs deliver identical objects every 10 min; only real changes can carry a new reason
			if op, ok := old.(*corev1.Pod); ok && op.ResourceVersion == o.(*corev1.Pod).ResourceVersion {
				return
			}
			w.onPod(o.(*corev1.Pod))
		},
	})
	evInf.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc: func(o any) { w.onEvent(o.(*corev1.Event)) },
	})
	f.Start(ctx.Done())
	f.WaitForCacheSync(ctx.Done())
	log.Println("informers synced")

	go func() { // discovery of CronJob specs
		for {
			var specs []CronJobSpec
			for _, o := range cjInf.GetStore().List() {
				cj := o.(*batchv1.CronJob)
				if w.inScope(cj.Namespace) {
					specs = append(specs, cronJobSpec(cj))
				}
			}
			if err := w.client.Post(ctx, "/agent/v1/k8s/cronjobs", map[string]any{"cronjobs": specs}, nil); err != nil {
				log.Printf("discovery: %v", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(discoveryEvery):
			}
		}
	}()
	for { // flush
		select {
		case <-ctx.Done():
			return
		case <-time.After(flushEvery):
		}
		if w.disk != nil {
			w.flushDisk(ctx)
		}
		w.mu.Lock()
		batch := w.pending
		w.pending = nil
		w.mu.Unlock()
		if len(batch) == 0 {
			continue
		}
		if err := w.client.Post(ctx, "/agent/v1/k8s/events", map[string]any{"events": batch}, nil); err != nil {
			log.Printf("flush: %v (re-queue %d)", err, len(batch))
			w.mu.Lock()
			w.pending = append(batch, w.pending...) // memory fallback (disk queue absent or full)
			if over := len(w.pending) - maxPending; over > 0 {
				log.Printf("flush: dropping %d oldest events (in-memory queue full)", over)
				w.pending = w.pending[over:]
			}
			w.mu.Unlock()
		}
	}
}

func (w *Watcher) onJob(j *batchv1.Job) {
	if !w.inScope(j.Namespace) {
		return
	}
	var cronUID, cronName string
	for _, o := range j.OwnerReferences {
		if o.Kind == "CronJob" {
			cronUID, cronName = string(o.UID), o.Name
		}
	}
	if cronUID == "" {
		return // standalone Job — TODO optional tracking
	}
	uid := string(j.UID)
	w.mu.Lock()
	w.jobToCron[uid] = cronUID
	seen := w.started[uid]
	done := w.finished[uid]
	w.mu.Unlock()
	if done {
		// R39: Update fires on every status write and on every 10-minute resync, for as long as the
		// Job is kept by the history limit — each of those used to re-send success/fail.
		return
	}
	base := ExecEvent{CronJobUID: cronUID, Namespace: j.Namespace, CronJob: cronName, JobName: j.Name, ExecutionID: "k8s-" + string(j.UID), AgentTS: time.Now().UTC(), Meta: map[string]string{}}
	if !seen && j.Status.StartTime != nil {
		w.mu.Lock()
		w.started[string(j.UID)] = true
		w.mu.Unlock()
		e := base
		e.Kind, e.Sequence, e.AgentTS = "start", 0, j.Status.StartTime.Time
		w.push(e)
	}
	if j.Status.CompletionTime != nil && j.Status.Succeeded > 0 {
		w.markFinished(uid)
		e := base
		e.Kind, e.Sequence, e.AgentTS = "success", 1, j.Status.CompletionTime.Time
		code := 0
		e.ExitCode = &code
		if j.Status.StartTime != nil {
			d := j.Status.CompletionTime.Sub(j.Status.StartTime.Time).Milliseconds()
			e.DurationMS = &d
		}
		w.push(e)
		return
	}
	for _, c := range j.Status.Conditions {
		if c.Type == batchv1.JobFailed && c.Status == corev1.ConditionTrue {
			w.markFinished(uid)
			e := base
			e.Kind, e.Sequence, e.Reason = "fail", 1, c.Reason
			code := 1
			e.ExitCode = &code
			e.StderrTail = c.Reason + ": " + c.Message
			end := c.LastTransitionTime.Time // when it failed, not when we happened to notice
			if end.IsZero() {
				end = time.Now()
			}
			e.AgentTS = end.UTC()
			if j.Status.StartTime != nil {
				d := end.Sub(j.Status.StartTime.Time).Milliseconds()
				e.DurationMS = &d
			}
			w.push(e)
			return // one terminal event per Job
		}
	}
}

func (w *Watcher) markFinished(uid string) {
	w.mu.Lock()
	w.finished[uid] = true
	w.mu.Unlock()
}

// forgetJob drops per-Job state when the Job object is deleted (history limit, TTL controller).
// Without it started/finished/jobToCron grew for the life of the process.
func (w *Watcher) forgetJob(o any) {
	if d, ok := o.(cache.DeletedFinalStateUnknown); ok {
		o = d.Obj
	}
	j, ok := o.(*batchv1.Job)
	if !ok {
		return
	}
	uid := string(j.UID)
	w.mu.Lock()
	delete(w.jobToCron, uid)
	delete(w.started, uid)
	delete(w.finished, uid)
	w.mu.Unlock()
}

// onPod enriches with node/pod + explicit failure reasons (OOMKilled etc.) for pods owned by tracked Jobs.
func (w *Watcher) onPod(p *corev1.Pod) {
	if !w.inScope(p.Namespace) {
		return
	}
	var jobUID string
	for _, o := range p.OwnerReferences {
		if o.Kind == "Job" {
			jobUID = string(o.UID)
		}
	}
	w.mu.Lock()
	cronUID, ok := w.jobToCron[jobUID]
	w.mu.Unlock()
	if !ok {
		return
	}
	for _, cs := range p.Status.ContainerStatuses {
		reason := ""
		if cs.State.Waiting != nil {
			reason = cs.State.Waiting.Reason
		}
		if cs.State.Terminated != nil && cs.State.Terminated.ExitCode != 0 {
			reason = cs.State.Terminated.Reason
		}
		if cs.LastTerminationState.Terminated != nil && cs.LastTerminationState.Terminated.Reason == "OOMKilled" {
			reason = "OOMKilled"
		}
		if podFailureReasons[reason] || p.Status.Reason == "Evicted" {
			if p.Status.Reason == "Evicted" {
				reason = "Evicted"
			}
			w.push(ExecEvent{Kind: "progress", CronJobUID: cronUID, Namespace: p.Namespace, JobName: p.Labels["job-name"], ExecutionID: "k8s-" + jobUID, Sequence: 5,
				AgentTS: time.Now().UTC(), Pod: p.Name, Node: p.Spec.NodeName, Reason: reason, Meta: map[string]string{"pod_phase": string(p.Status.Phase)}})
		}
	}
}

// onEvent forwards warning events (FailedScheduling, node pressure, FailedMount/PVC, DNS) attached to tracked pods/jobs.
func (w *Watcher) onEvent(ev *corev1.Event) {
	ts := ev.LastTimestamp.Time
	if ts.IsZero() { // events.k8s.io-style events only set EventTime / series
		ts = ev.EventTime.Time
	}
	if ev.Type != corev1.EventTypeWarning || !w.inScope(ev.Namespace) || ts.IsZero() || time.Since(ts) > 2*time.Minute {
		return
	}
	interesting := map[string]bool{"FailedScheduling": true, "FailedMount": true, "FailedAttachVolume": true, "NodeNotReady": true, "Evicted": true, "BackOff": true, "DNSConfigForming": true, "FailedCreatePodSandBox": true}
	if !interesting[ev.Reason] {
		return
	}
	w.push(ExecEvent{Kind: "k8s_event", Namespace: ev.Namespace, JobName: ev.InvolvedObject.Name, AgentTS: ts, Reason: ev.Reason,
		// R39: was string(rune(ev.Count)) — that is the Unicode code point, so a count of 65 was "A".
		StderrTail: ev.Message, Meta: map[string]string{"object_kind": ev.InvolvedObject.Kind, "count": strconv.Itoa(int(ev.Count))}})
}
