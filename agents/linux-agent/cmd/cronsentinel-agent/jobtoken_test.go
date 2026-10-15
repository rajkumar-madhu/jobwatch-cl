package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Review #3: the token can come from a 0600 file or the environment instead of argv.
func TestJobTokenFrom(t *testing.T) {
	p := filepath.Join(t.TempDir(), "tok")
	os.WriteFile(p, []byte("  tok-123 \nignored\n"), 0o600)
	if got := jobTokenFrom(p, "env-tok"); got != "tok-123" {
		t.Fatalf("file: %q", got)
	}
	if got := jobTokenFrom("", " env-tok "); got != "env-tok" {
		t.Fatalf("env: %q", got)
	}
	if got := jobTokenFrom(filepath.Join(t.TempDir(), "missing"), "env-tok"); got != "env-tok" {
		t.Fatalf("missing file: %q", got)
	}
}

// spool_uid/spool_user are the daemon's word: a user cannot pre-fill them to impersonate root.
func TestSpoolAnnotateOverwritesWriterClaims(t *testing.T) {
	raw := []byte(`{"kind":"success","job_token":"t","meta":{"spool_uid":"0","spool_user":"root","command":"x"}}`)
	out := string(spoolAnnotate("h")(raw, 4242))
	if !strings.Contains(out, `"spool_uid":"4242"`) || strings.Contains(out, `"spool_user":"root"`) {
		t.Fatalf("writer claims survived: %s", out)
	}
}
