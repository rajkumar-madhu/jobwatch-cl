package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
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

func testClient(t *testing.T, h http.HandlerFunc) *Client {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	cfg := Defaults()
	cfg.ServerURL, cfg.AgentID, cfg.AgentKey = srv.URL, "agent-1", "secret-key"
	return NewClient(cfg)
}

func status(code int) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) { http.Error(w, "nope", code) }
}

// The flusher treats FatalError as "stop, the key is revoked" and anything else as "back off and
// retry the same batch". These pin which responses land on which side.
func TestAuthRejectionIsFatal(t *testing.T) {
	for _, code := range []int{401, 403} {
		err := testClient(t, status(code)).SendEvents(context.Background(), nil)
		if err == nil || !IsFatal(err) {
			t.Errorf("%d: want fatal, got %v", code, err)
		}
	}
}

func TestServerAndTransientErrorsAreRetryable(t *testing.T) {
	for _, code := range []int{400, 404, 408, 413, 429, 500, 502, 503} {
		err := testClient(t, status(code)).SendEvents(context.Background(), nil)
		if err == nil || IsFatal(err) {
			t.Errorf("%d: want retryable error, got %v", code, err)
		}
	}
}

func TestNetworkErrorIsRetryable(t *testing.T) {
	srv := httptest.NewServer(status(200))
	url := srv.URL
	srv.Close()
	cfg := Defaults()
	cfg.ServerURL = url
	err := NewClient(cfg).SendEvents(context.Background(), nil)
	if err == nil || IsFatal(err) {
		t.Fatalf("want retryable network error, got %v", err)
	}
}

func TestCancelledContextIsNotFatal(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := testClient(t, status(200)).SendEvents(ctx, nil)
	if err == nil || IsFatal(err) || !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}

func TestIsFatalSeesThroughWrapping(t *testing.T) {
	if !IsFatal(fmt.Errorf("send: %w", &FatalError{"auth"})) {
		t.Fatal("wrapped FatalError not recognised")
	}
	if IsFatal(errors.New("server 500")) || IsFatal(nil) {
		t.Fatal("plain error treated as fatal")
	}
}

func TestSendEventsSendsBatchVerbatimWithAgentHeaders(t *testing.T) {
	var got struct {
		Events []json.RawMessage `json:"events"`
	}
	var hdr http.Header
	c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		hdr = r.Header.Clone()
		if r.URL.Path != "/agent/v1/events" || r.Method != "POST" {
			t.Errorf("%s %s", r.Method, r.URL.Path)
		}
		json.NewDecoder(r.Body).Decode(&got)
	})
	batch := []json.RawMessage{json.RawMessage(`{"kind":"start","sequence":1}`), json.RawMessage(`{"kind":"success"}`)}
	if err := c.SendEvents(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
	if len(got.Events) != 2 || string(got.Events[0]) != `{"kind":"start","sequence":1}` {
		t.Fatalf("events %s", got.Events)
	}
	if hdr.Get("X-Agent-Key") != "secret-key" || hdr.Get("X-Agent-Id") != "agent-1" || hdr.Get("Content-Type") != "application/json" {
		t.Fatalf("headers %v", hdr)
	}
}

func TestSendDiscoveryDecodesTokens(t *testing.T) {
	c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"tokens":{"fp1":"tok1"}}`))
	})
	tok, err := c.SendDiscovery(context.Background(), []DiscoveredJob{{Fingerprint: "fp1"}})
	if err != nil || tok["fp1"] != "tok1" {
		t.Fatalf("tok=%v err=%v", tok, err)
	}
}

func TestErrorBodyIsBoundedInTheMessage(t *testing.T) {
	c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		io.WriteString(w, string(make([]byte, 4<<20)))
	})
	err := c.SendEvents(context.Background(), nil)
	if err == nil || len(err.Error()) > (1<<20)+64 {
		t.Fatalf("error message not bounded: %d bytes", len(err.Error()))
	}
}

func TestEnrollReturnsKeyAndRejectsBadToken(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b map[string]string
		json.NewDecoder(r.Body).Decode(&b)
		if b["bootstrap_token"] != "good" {
			http.Error(w, "bad token", 401)
			return
		}
		w.Write([]byte(`{"agent_id":"a1","agent_key":"k1"}`))
	}))
	defer srv.Close()
	cfg, err := Enroll(context.Background(), srv.URL, "good", "host", "hid", false)
	if err != nil || cfg.AgentID != "a1" || cfg.AgentKey != "k1" {
		t.Fatalf("cfg=%+v err=%v", cfg, err)
	}
	if _, err := Enroll(context.Background(), srv.URL, "bad", "host", "hid", false); !IsFatal(err) {
		t.Fatalf("bad bootstrap token: want fatal, got %v", err)
	}
}

// Review #21: Go forwards custom headers such as X-Agent-Key on redirects, including to another host.
func TestAgentKeyIsNotSentToRedirectTarget(t *testing.T) {
	knownBug(t, "review #21", func() error {
		var leaked atomic.Value
		leaked.Store("")
		other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			leaked.Store(r.Header.Get("X-Agent-Key"))
		}))
		defer other.Close()
		c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
			// 127.0.0.1 vs localhost: a different host as far as the redirect policy is concerned
			_, port, _ := net.SplitHostPort(other.Listener.Addr().String())
			http.Redirect(w, r, "http://localhost:"+port+"/steal", http.StatusTemporaryRedirect)
		})
		c.SendEvents(context.Background(), nil)
		if k := leaked.Load().(string); k != "" {
			return fmt.Errorf("agent key %q was sent to the redirect target", k)
		}
		return nil
	})
}
