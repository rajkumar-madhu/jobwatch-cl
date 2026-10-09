package buffer

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func open(t *testing.T) (*Buffer, string) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "buf.jsonl")
	b, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { b.Close() })
	return b, p
}

func TestCorruptAndOversizedLinesAreSkippedNotFatal(t *testing.T) {
	b, p := open(t)
	b.Push(map[string]int{"a": 1})
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0)
	f.WriteString(`{"torn":` + "\n")                                    // crash mid-write
	f.WriteString(`{"x":"` + strings.Repeat("y", MaxLine+10) + "\"}\n") // over the limit
	f.Close()
	b.Push(map[string]int{"b": 2})
	lines, next, err := b.Peek(10)
	if err != nil {
		t.Fatalf("Peek must not fail on bad lines: %v", err)
	}
	if len(lines) != 2 {
		t.Fatalf("want the 2 valid lines, got %d", len(lines))
	}
	if _, err := json.Marshal(map[string]any{"events": lines}); err != nil {
		t.Fatalf("batch must always marshal: %v", err)
	}
	if b.Skipped != 2 {
		t.Fatalf("skipped=%d", b.Skipped)
	}
	b.Ack(next)
	if l, _, _ := b.Peek(10); len(l) != 0 {
		t.Fatalf("bad lines must be consumed with the batch, got %d left", len(l))
	}
}

func TestPartialTailIsLeftForLater(t *testing.T) {
	b, p := open(t)
	b.Push(map[string]int{"a": 1})
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0)
	f.WriteString(`{"in":"flight"`) // no newline yet
	f.Close()
	lines, next, _ := b.Peek(10)
	if len(lines) != 1 {
		t.Fatalf("got %d", len(lines))
	}
	b.Ack(next)
	f, _ = os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0)
	f.WriteString("}\n")
	f.Close()
	lines, _, _ = b.Peek(10)
	if len(lines) != 1 || string(lines[0]) != `{"in":"flight"}` {
		t.Fatalf("partial tail lost or mangled: %q", lines)
	}
}

func TestConcurrentWritersAcrossHandlesLoseNothingDuringCompaction(t *testing.T) {
	// Two independent handles on the same file model the daemon and a cs-run process.
	daemon, p := open(t)
	writer, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	big := strings.Repeat("z", 64<<10)
	const total = 400 // 400 × 64 KiB ≈ 25 MiB → forces several compactions
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < total; i++ {
			writer.Push(map[string]any{"i": i, "pad": big})
		}
	}()
	seen := map[int]bool{}
	deadline := time.Now().Add(30 * time.Second)
	for len(seen) < total && time.Now().Before(deadline) {
		lines, next, err := daemon.Peek(50)
		if err != nil {
			t.Fatal(err)
		}
		for _, l := range lines {
			var v struct{ I int }
			json.Unmarshal(l, &v)
			seen[v.I] = true
		}
		daemon.Ack(next)
	}
	wg.Wait()
	for {
		lines, next, _ := daemon.Peek(50)
		if len(lines) == 0 {
			break
		}
		for _, l := range lines {
			var v struct{ I int }
			json.Unmarshal(l, &v)
			seen[v.I] = true
		}
		daemon.Ack(next)
	}
	if len(seen) != total {
		t.Fatalf("lost %d events across compaction", total-len(seen))
	}
}

func TestSpoolImportRejectsSymlinksAndImportsCompleteFiles(t *testing.T) {
	b, _ := open(t)
	dir := t.TempDir()
	w, err := OpenSpool(dir, "exec1")
	if err != nil {
		t.Fatal(err)
	}
	w.Push(map[string]string{"kind": "start"})
	w.Push(map[string]string{"kind": "success"})
	w.Close()
	os.Symlink("/etc/shadow", filepath.Join(dir, "evil.jsonl"))
	n, err := b.ImportSpool(dir, func(os.FileInfo) bool { return true }, func(raw []byte, _ uint32) []byte { return raw })
	if err != nil || n != 2 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if ents, _ := os.ReadDir(dir); len(ents) != 0 {
		t.Fatalf("spool not emptied: %v", ents)
	}
	if _, err := OpenSpool(dir, "../x"); err == nil {
		t.Fatal("path traversal in spool name accepted")
	}
}
