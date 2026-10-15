package watcher

import (
	"encoding/json"
	"errors"
	"hash/fnv"
)

// batchControl sizes disk-queue batches so one event the server cannot take never blocks delivery.
// Same policy as the Linux agent's agent.BatchControl: halve a batch the server rejects (400/413/422
// at once, 500 after three strikes on the same head), drop a lone rejected event only if an empty
// batch succeeds, and never drop anything on network errors or 408/429/502/503/504.
type batchControl struct {
	full, limit int
	head        uint64
	strikes     int
}

const serverErrorStrikes = 3

func newBatchControl(full int) *batchControl { return &batchControl{full: full, limit: full} }

func (c *batchControl) succeeded() {
	c.strikes = 0
	if c.limit *= 2; c.limit > c.full {
		c.limit = c.full
	}
}

// failed reports whether the single event at the head of the failed batch should be dropped.
func (c *batchControl) failed(err error, head uint64, n int, probe func() error) bool {
	var he *HTTPError
	if !errors.As(err, &he) {
		return false
	}
	switch he.Status {
	case 400, 413, 422:
	case 500:
		if head != c.head {
			c.head, c.strikes = head, 0
		}
		if c.strikes++; c.strikes < serverErrorStrikes {
			return false
		}
	default:
		return false
	}
	c.strikes = 0
	if n > 1 {
		c.limit = n / 2
		return false
	}
	if probe != nil && probe() != nil {
		return false
	}
	c.limit = c.full
	return true
}

// headKey identifies a batch head without needing the queue's offset.
func headKey(line json.RawMessage) uint64 {
	h := fnv.New64a()
	h.Write(line)
	return h.Sum64()
}
