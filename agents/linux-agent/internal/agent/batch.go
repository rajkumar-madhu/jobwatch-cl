package agent

import "errors"

// BatchControl sizes event batches so one event the server cannot take never blocks delivery.
//
// Before, every non-auth error retried the same batch head forever: a single malformed event that
// made the server answer 500 (or 4xx) stopped all delivery from the host for good. Now a batch the
// server rejects is halved until the bad event is alone, and that event is dropped (logged by the
// caller) once the server shows it is otherwise healthy. Outages (network errors, 408/429/502/503/
// 504, a 500 that an empty batch also gets) never drop anything.
type BatchControl struct {
	full, limit int
	head        int64 // buffer offset the 500 strikes were counted at
	strikes     int
}

// serverErrorStrikes is how many 500s in a row on the same batch head count as "this batch".
const serverErrorStrikes = 3

func NewBatchControl(full int) *BatchControl {
	if full < 1 {
		full = 1
	}
	return &BatchControl{full: full, limit: full}
}

// Limit is how many events to send next.
func (c *BatchControl) Limit() int { return c.limit }

// Succeeded grows the batch back toward full size after an accepted send.
func (c *BatchControl) Succeeded() {
	c.strikes = 0
	if c.limit *= 2; c.limit > c.full {
		c.limit = c.full
	}
}

// Failed records that a send of n events starting at buffer offset head failed with err. It
// returns true when that single event should be dropped. probe sends an empty batch: it tells a
// poison event (probe succeeds) from a server that fails everything (probe fails).
func (c *BatchControl) Failed(err error, head int64, n int, probe func() error) (drop bool) {
	var he *HTTPError
	if !errors.As(err, &he) {
		return false // network error or cancellation: transient
	}
	switch he.Status {
	case 400, 413, 422: // the server says the request content is wrong
	case 500:
		if head != c.head {
			c.head, c.strikes = head, 0
		}
		if c.strikes++; c.strikes < serverErrorStrikes {
			return false
		}
	default:
		return false // outage, overload, wrong URL: retry the same batch later
	}
	c.strikes = 0
	if n > 1 {
		c.limit = n / 2
		return false
	}
	if probe != nil && probe() != nil {
		return false // the server rejects everything right now, not just this event
	}
	c.limit = c.full
	return true
}
