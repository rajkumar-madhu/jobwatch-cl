package agent

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

// Review #4: one event the server cannot take used to stop delivery forever. Simulates the daemon's
// flush loop against a server that answers 500 to any batch containing the poison event.
func TestPoisonEventIsIsolatedAndDropped(t *testing.T) {
	for _, code := range []int{500, 422, 400} {
		var mu sync.Mutex
		var delivered []int
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var body struct{ Events []json.RawMessage }
			json.NewDecoder(r.Body).Decode(&body)
			var batch []int
			for _, e := range body.Events {
				var v struct{ I int }
				if json.Unmarshal(e, &v) != nil || v.I == 37 {
					http.Error(w, "bad event", code)
					return
				}
				batch = append(batch, v.I)
			}
			mu.Lock()
			delivered = append(delivered, batch...)
			mu.Unlock()
		}))
		cfg := Defaults()
		cfg.ServerURL = srv.URL
		c := NewClient(cfg)

		queue := make([]json.RawMessage, 100)
		for i := range queue {
			queue[i], _ = json.Marshal(map[string]int{"i": i})
		}
		head, dropped := 0, 0
		bc := NewBatchControl(100)
		for round := 0; head < len(queue) && round < 500; round++ {
			n := min(bc.Limit(), len(queue)-head)
			batch := queue[head : head+n]
			if err := c.SendEvents(context.Background(), batch); err != nil {
				if bc.Failed(err, int64(head), n, func() error { return c.SendEvents(context.Background(), nil) }) {
					head++
					dropped++
				}
				continue
			}
			head += n
			bc.Succeeded()
		}
		srv.Close()
		if head != len(queue) || dropped != 1 || len(delivered) != 99 {
			t.Fatalf("%d: head=%d dropped=%d delivered=%d", code, head, dropped, len(delivered))
		}
		if bc.Limit() != 100 {
			t.Errorf("%d: batch size not restored: %d", code, bc.Limit())
		}
	}
}

func TestOutagesNeverDropEvents(t *testing.T) {
	failAll := func() error { return &HTTPError{Status: 500} }
	bc := NewBatchControl(8)
	for i := 0; i < 50; i++ { // a server that 500s on everything, including the empty probe
		if bc.Failed(&HTTPError{Status: 500}, 0, bc.Limit(), failAll) {
			t.Fatal("dropped an event during an outage")
		}
	}
	for _, err := range []error{&HTTPError{Status: 502}, &HTTPError{Status: 503}, &HTTPError{Status: 429},
		&HTTPError{Status: 404}, errors.New("connection refused"), context.Canceled} {
		bc := NewBatchControl(8)
		for i := 0; i < 20; i++ {
			if bc.Failed(err, 0, 1, nil) || bc.Limit() != 8 {
				t.Fatalf("%v: dropped or shrank the batch", err)
			}
		}
	}
}

func TestServerErrorNeedsRepeatedStrikesOnTheSameHead(t *testing.T) {
	bc := NewBatchControl(8)
	ok := func() error { return nil }
	// different heads: progress is being made, so never treat it as poison
	for head := int64(0); head < 10; head++ {
		if bc.Failed(&HTTPError{Status: 500}, head, 8, ok) || bc.Limit() != 8 {
			t.Fatal("shrank on a single 500")
		}
	}
	bc.Failed(&HTTPError{Status: 500}, 99, 8, ok)
	bc.Failed(&HTTPError{Status: 500}, 99, 8, ok)
	if bc.Limit() != 8 {
		t.Fatal("shrank before 3 strikes")
	}
	bc.Failed(&HTTPError{Status: 500}, 99, 8, ok)
	if bc.Limit() != 4 {
		t.Fatalf("limit %d after 3 strikes, want 4", bc.Limit())
	}
}
