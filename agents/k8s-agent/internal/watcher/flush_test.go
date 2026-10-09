package watcher

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/cronsentinel/k8s-agent/internal/buffer"
)

// Events queued while ingest is down are delivered once it recovers — and survive a restart
// (a new Watcher on the same file) in between.
func TestDiskQueueSurvivesOutageAndRestart(t *testing.T) {
	var up atomic.Bool
	var mu sync.Mutex
	var got []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !up.Load() {
			http.Error(w, "down", http.StatusServiceUnavailable)
			return
		}
		b, _ := io.ReadAll(r.Body)
		var body struct{ Events []ExecEvent }
		json.Unmarshal(b, &body)
		mu.Lock()
		for _, e := range body.Events {
			got = append(got, e.ExecutionID)
		}
		mu.Unlock()
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	path := filepath.Join(t.TempDir(), "events.jsonl")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	b1, err := buffer.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	w1 := New(nil, NewClient(srv.URL, "a", "k", false), nil).WithDiskQueue(b1)
	for _, id := range []string{"e1", "e2", "e3"} {
		w1.push(ExecEvent{Kind: "success", ExecutionID: id})
	}
	w1.flushDisk(ctx) // ingest down: nothing may be lost
	b1.Close()        // "restart"

	up.Store(true)
	b2, _ := buffer.Open(path)
	defer b2.Close()
	w2 := New(nil, NewClient(srv.URL, "a", "k", false), nil).WithDiskQueue(b2)
	w2.flushDisk(ctx)
	w2.flushDisk(ctx) // second flush must not resend
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 3 || got[0] != "e1" || got[2] != "e3" {
		t.Fatalf("delivered %v, want e1 e2 e3 exactly once", got)
	}
}
