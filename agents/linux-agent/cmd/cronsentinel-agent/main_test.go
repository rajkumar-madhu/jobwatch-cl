package main

import (
	"encoding/json"
	"os/user"
	"testing"

	"github.com/cronsentinel/linux-agent/internal/discovery"
)

// A user who spools an event claiming root's backup fingerprint must not be able to report it:
// the daemon re-derives the fingerprint from the spool file's owner.
func TestSpoolAnnotateBindsFingerprintToFileOwner(t *testing.T) {
	rootFP := discovery.Fingerprint("h1", "root", "/usr/local/bin/backup.sh", "0 2 * * *")
	raw, _ := json.Marshal(map[string]any{"kind": "success", "fingerprint": rootFP,
		"meta": map[string]string{"command": "/usr/local/bin/backup.sh", "schedule": "0 2 * * *"}})
	// UID 65534 is nobody on Linux, but can be absent or named differently on other hosts.
	ownerName := ""
	if owner, err := user.LookupId("65534"); err == nil {
		ownerName = owner.Username
	}
	out := spoolAnnotate("h1")(raw, 65534)
	var ev map[string]any
	json.Unmarshal(out, &ev)
	if ev["fingerprint"] == rootFP {
		t.Fatal("non-root spool event kept root's fingerprint")
	}
	want := discovery.Fingerprint("h1", ownerName, "/usr/local/bin/backup.sh", "0 2 * * *")
	if ev["fingerprint"] != want {
		t.Fatalf("fingerprint %v, want owner-derived %v", ev["fingerprint"], want)
	}
	if m := ev["meta"].(map[string]any); m["spool_uid"] != "65534" {
		t.Fatalf("meta %v", m)
	}
	// root's own spooled event keeps the right fingerprint
	var ev2 map[string]any
	json.Unmarshal(spoolAnnotate("h1")(raw, 0), &ev2)
	if ev2["fingerprint"] != rootFP {
		t.Fatalf("root event fingerprint changed: %v", ev2["fingerprint"])
	}
}
