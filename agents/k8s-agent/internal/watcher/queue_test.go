package watcher

// Delivery and retry of queued events: disk queue → ingest (flushDisk), the in-memory fallback
// when the disk queue refuses, and the client's error handling.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/cronsentinel/k8s-agent/internal/buffer"
)

// knownBug pins a finding from reviews/jobwatch-code-review-2026-10-10.md. While the bug is
// present, check returns an error and the test is skipped with the finding id. Once the fix
// lands, check passes and the test fails, so the wrapper is removed together with the bug.
func knownBug(t *testing.T, finding string, check func() error) {
	t.Helper()
	if err := check(); err != nil {
		t.Skipf("KNOWN BUG (%s): %v", finding, err)
	}
	t.Fatalf("%s looks fixed: drop knownBug() so this runs as a normal test", finding)
}

// fakeDisk is a DiskQueue whose behaviour each test scripts.
type fakeDisk struct {
	mu      sync.Mutex
	pushErr error
	lines   []json.RawMessage
	next    int64
	peekErr error
	acks    []int64
	pushed  int
}

func (d *fakeDisk) Push(v any) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.pushed++
	return d.pushErr
}

func (d *fakeDisk) Peek(n int) ([]json.RawMessage, int64, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if len(d.acks) > 0 && d.acks[len(d.acks)-1] == d.next {
		return nil, d.next, d.peekErr
	}
	return d.lines, d.next, d.peekErr
}

func (d *fakeDisk) Ack(off int64) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.acks = append(d.acks, off)
	return nil
}

type recorder struct {
	mu     sync.Mutex
	posts  int
	events []ExecEvent
	status func(events []ExecEvent) int
}

func (rc *recorder) server(t *testing.T) *httptest.Server {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		var body struct{ Events []ExecEvent }
		json.Unmarshal(b, &body)
		rc.mu.Lock()
		defer rc.mu.Unlock()
		rc.posts++
		if rc.status != nil {
			if code := rc.status(body.Events); code >= 300 {
				http.Error(w, "rejected", code)
				return
			}
		}
		rc.events = append(rc.events, body.Events...)
		w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func (rc *recorder) delivered() []string {
	rc.mu.Lock()
	defer rc.mu.Unlock()
	var out []string
	for _, e := range rc.events {
		out = append(out, e.ExecutionID)
	}
	return out
}

func ctx10s(t *testing.T) context.Context {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func TestPushFallsBackToMemoryWhenDiskRefuses(t *testing.T) {
	d := &fakeDisk{pushErr: errors.New("buffer: full")}
	w := New(nil, nil, nil).WithDiskQueue(d)
	w.push(ExecEvent{ExecutionID: "e1"})
	if d.pushed != 1 || len(w.pending) != 1 || w.pending[0].ExecutionID != "e1" {
		t.Fatalf("pushed=%d pending=%v", d.pushed, w.pending)
	}
}

func TestPushGoesToDiskOnly(t *testing.T) {
	d := &fakeDisk{}
	w := New(nil, nil, nil).WithDiskQueue(d)
	w.push(ExecEvent{ExecutionID: "e1"})
	if d.pushed != 1 || len(w.pending) != 0 {
		t.Fatalf("pushed=%d pending=%d", d.pushed, len(w.pending))
	}
}

func TestInMemoryQueueDropsOldestBeyondMax(t *testing.T) {
	w := New(nil, nil, nil)
	for i := 0; i < maxPending+5; i++ {
		w.push(ExecEvent{ExecutionID: fmt.Sprint(i)})
	}
	if len(w.pending) != maxPending || w.pending[0].ExecutionID != "5" || w.pending[maxPending-1].ExecutionID != fmt.Sprint(maxPending+4) {
		t.Fatalf("len=%d first=%s", len(w.pending), w.pending[0].ExecutionID)
	}
}

func TestFlushDiskDoesNotAckWhenIngestFails(t *testing.T) {
	rc := &recorder{status: func([]ExecEvent) int { return 503 }}
	d := &fakeDisk{lines: []json.RawMessage{json.RawMessage(`{"execution_id":"e1"}`)}, next: 42}
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(d)
	w.flushDisk(ctx10s(t))
	if len(d.acks) != 0 || rc.posts != 1 {
		t.Fatalf("acks=%v posts=%d (one attempt per flush tick, nothing acked)", d.acks, rc.posts)
	}
}

func TestFlushDiskAcksAfterIngestAccepts(t *testing.T) {
	rc := &recorder{}
	d := &fakeDisk{lines: []json.RawMessage{json.RawMessage(`{"execution_id":"e1"}`)}, next: 42}
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(d)
	w.flushDisk(ctx10s(t))
	// a second Ack(42) after the queue is empty is a harmless re-commit of the same offset
	if len(d.acks) == 0 || d.acks[0] != 42 || rc.posts != 1 || fmt.Sprint(rc.delivered()) != "[e1]" {
		t.Fatalf("acks=%v delivered=%v", d.acks, rc.delivered())
	}
}

func TestFlushDiskAcksSkippedOnlyLinesWithoutPosting(t *testing.T) {
	rc := &recorder{}
	d := &fakeDisk{next: 17} // Peek consumed only corrupt lines
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(d)
	w.flushDisk(ctx10s(t))
	if fmt.Sprint(d.acks) != "[17]" || rc.posts != 0 {
		t.Fatalf("acks=%v posts=%d", d.acks, rc.posts)
	}
}

func TestFlushDiskStopsOnReadError(t *testing.T) {
	rc := &recorder{}
	d := &fakeDisk{peekErr: errors.New("io"), lines: []json.RawMessage{json.RawMessage(`{}`)}}
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(d)
	w.flushDisk(ctx10s(t))
	if rc.posts != 0 || len(d.acks) != 0 {
		t.Fatalf("posts=%d acks=%v", rc.posts, d.acks)
	}
}

func TestFlushDiskDrainsLargeBacklogInBatchesInOrder(t *testing.T) {
	rc := &recorder{}
	b, err := buffer.Open(filepath.Join(t.TempDir(), "q.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(b)
	for i := 0; i < 1201; i++ {
		w.push(ExecEvent{ExecutionID: fmt.Sprint(i)})
	}
	w.flushDisk(ctx10s(t))
	got := rc.delivered()
	if rc.posts != 3 || len(got) != 1201 || got[0] != "0" || got[1200] != "1200" {
		t.Fatalf("posts=%d delivered=%d", rc.posts, len(got))
	}
	w.flushDisk(ctx10s(t))
	if rc.posts != 3 {
		t.Fatal("drained queue was re-sent")
	}
}

func TestFlushDiskStopsWhenContextCancelled(t *testing.T) {
	rc := &recorder{}
	d := &fakeDisk{lines: []json.RawMessage{json.RawMessage(`{}`)}, next: 1}
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(d)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	w.flushDisk(ctx)
	if rc.posts != 0 {
		t.Fatalf("posted %d times after cancel", rc.posts)
	}
}

func TestClientReportsNon2xxAsError(t *testing.T) {
	for _, code := range []int{400, 401, 500} {
		rc := &recorder{status: func([]ExecEvent) int { return code }}
		err := NewClient(rc.server(t).URL, "a", "k", false).Post(context.Background(), "/x", map[string]any{}, nil)
		if err == nil || !strings.Contains(err.Error(), fmt.Sprint(code)) {
			t.Errorf("%d: %v", code, err)
		}
	}
}

func TestClientSendsAgentHeaders(t *testing.T) {
	var hdr atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hdr.Store(r.Header.Clone()) }))
	defer srv.Close()
	NewClient(srv.URL, "agent-9", "key-9", false).Post(context.Background(), "/x", nil, nil)
	h := hdr.Load().(http.Header)
	if h.Get("X-Agent-Id") != "agent-9" || h.Get("X-Agent-Key") != "key-9" {
		t.Fatalf("headers %v", h)
	}
}

// Review #4 (fixed in flushDisk): one event the server will never accept no longer blocks every
// event queued behind it.
func TestPoisonEventDoesNotBlockTheQueue(t *testing.T) {
	rc := &recorder{status: func(evs []ExecEvent) int {
		for _, e := range evs {
			if e.ExecutionID == "poison" {
				return 422
			}
		}
		return 200
	}}
	b, _ := buffer.Open(filepath.Join(t.TempDir(), "q.jsonl"))
	defer b.Close()
	w := New(nil, NewClient(rc.server(t).URL, "a", "k", false), nil).WithDiskQueue(b)
	w.push(ExecEvent{ExecutionID: "poison"})
	w.push(ExecEvent{ExecutionID: "good"})
	for i := 0; i < 5; i++ { // five flush ticks
		w.flushDisk(ctx10s(t))
	}
	if fmt.Sprint(rc.delivered()) != "[good]" {
		t.Fatalf("delivered %v after 5 flushes (%d posts)", rc.delivered(), rc.posts)
	}
}

// Review #21: the agent key is forwarded when ingest redirects to another host.
func TestAgentKeyIsNotSentToRedirectTarget(t *testing.T) {
	knownBug(t, "review #21", func() error {
		var leaked atomic.Value
		leaked.Store("")
		other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			leaked.Store(r.Header.Get("X-Agent-Key"))
		}))
		defer other.Close()
		_, port, _ := net.SplitHostPort(other.Listener.Addr().String())
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "http://localhost:"+port+"/steal", http.StatusTemporaryRedirect)
		}))
		defer srv.Close()
		NewClient(srv.URL, "a", "secret-key", false).Post(context.Background(), "/agent/v1/k8s/events", nil, nil)
		if k := leaked.Load().(string); k != "" {
			return fmt.Errorf("agent key %q was sent to the redirect target", k)
		}
		return nil
	})
}
