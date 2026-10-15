package watcher

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/cronsentinel/k8s-agent/internal/buffer"
)

// Review #4: one event the ingest tier rejects used to stop flushDisk for good.
func TestFlushDiskIsolatesAndDropsPoisonEvent(t *testing.T) {
	for _, code := range []int{500, 422} {
		var mu sync.Mutex
		var got []string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			b, _ := io.ReadAll(r.Body)
			var body struct{ Events []ExecEvent }
			json.Unmarshal(b, &body)
			for _, e := range body.Events {
				if e.ExecutionID == "poison" {
					http.Error(w, "bad", code)
					return
				}
			}
			mu.Lock()
			for _, e := range body.Events {
				got = append(got, e.ExecutionID)
			}
			mu.Unlock()
			w.Write([]byte(`{}`))
		}))
		q, err := buffer.Open(filepath.Join(t.TempDir(), "q.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		w := New(nil, NewClient(srv.URL, "a", "k", false), nil).WithDiskQueue(q)
		for i := 0; i < 40; i++ {
			id := "e" + string(rune('A'+i))
			if i == 23 {
				id = "poison"
			}
			w.push(ExecEvent{Kind: "success", ExecutionID: id})
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		for tick := 0; tick < 100; tick++ { // each call is one flush tick
			w.flushDisk(ctx)
		}
		cancel()
		srv.Close()
		q.Close()
		mu.Lock()
		if len(got) != 39 {
			t.Fatalf("%d: delivered %d of 39 good events", code, len(got))
		}
		for _, id := range got {
			if id == "poison" {
				t.Fatalf("%d: poison event delivered", code)
			}
		}
		mu.Unlock()
	}
}

// An outage must never drop events, even after many ticks.
func TestFlushDiskKeepsEverythingDuringOutage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "db down", 500)
	}))
	defer srv.Close()
	q, _ := buffer.Open(filepath.Join(t.TempDir(), "q.jsonl"))
	defer q.Close()
	w := New(nil, NewClient(srv.URL, "a", "k", false), nil).WithDiskQueue(q)
	for _, id := range []string{"a", "b", "c"} {
		w.push(ExecEvent{Kind: "success", ExecutionID: id})
	}
	for tick := 0; tick < 50; tick++ {
		w.flushDisk(context.Background())
	}
	if l, _, _ := q.Peek(10); len(l) != 3 {
		t.Fatalf("%d events left, want 3", len(l))
	}
}
