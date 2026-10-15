package buffer

// Retry contract between the watcher's flushDisk and the durable queue: Peek is idempotent until
// Ack, the offset survives restarts, compaction frees space under the MaxBytes cap, and the cap
// counts what it refuses.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func openTmp(t *testing.T) (*Buffer, string) {
	t.Helper()
	p := filepath.Join(t.TempDir(), "q.jsonl")
	b, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { b.Close() })
	return b, p
}

func ids(t *testing.T, lines []json.RawMessage) string {
	t.Helper()
	var out []int
	for _, l := range lines {
		var v struct{ I int }
		json.Unmarshal(l, &v)
		out = append(out, v.I)
	}
	return fmt.Sprint(out)
}

func withMaxBytes(t *testing.T, n int64) {
	old := MaxBytes
	MaxBytes = n
	t.Cleanup(func() { MaxBytes = old })
}

func TestPeekIsIdempotentUntilAck(t *testing.T) {
	b, _ := openTmp(t)
	for i := 0; i < 4; i++ {
		b.Push(map[string]int{"i": i})
	}
	l1, n1, _ := b.Peek(2)
	l2, n2, _ := b.Peek(2)
	if ids(t, l1) != "[0 1]" || ids(t, l2) != "[0 1]" || n1 != n2 {
		t.Fatalf("%s@%d vs %s@%d", ids(t, l1), n1, ids(t, l2), n2)
	}
	b.Ack(n1)
	if l, _, _ := b.Peek(10); ids(t, l) != "[2 3]" {
		t.Fatalf("after ack %s", ids(t, l))
	}
}

func TestOffsetSurvivesRestartAndStaleOffsetResets(t *testing.T) {
	b, p := openTmp(t)
	for i := 0; i < 3; i++ {
		b.Push(map[string]int{"i": i})
	}
	_, next, _ := b.Peek(1)
	b.Ack(next)
	b.Close()
	b2, _ := Open(p)
	if l, _, _ := b2.Peek(10); ids(t, l) != "[1 2]" {
		t.Fatalf("after restart %s", ids(t, l))
	}
	b2.Close()

	os.WriteFile(p+".offset", []byte("123456789"), 0o640)
	b3, _ := Open(p)
	defer b3.Close()
	if l, _, _ := b3.Peek(10); ids(t, l) != "[0 1 2]" {
		t.Fatalf("offset past EOF must reset to 0, got %s", ids(t, l))
	}
}

func TestCompactionAt256KiBOnlyWhenFullyAcked(t *testing.T) {
	b, p := openTmp(t)
	pad := strings.Repeat("x", 32<<10)
	for i := 0; i < 10; i++ { // ≈ 320 KiB
		b.Push(map[string]any{"i": i, "pad": pad})
	}
	_, next, _ := b.Peek(9)
	b.Ack(next)
	if st, _ := os.Stat(p); st.Size() == 0 {
		t.Fatal("compacted with one event unacked")
	}
	_, next, _ = b.Peek(10)
	b.Ack(next)
	if st, _ := os.Stat(p); st.Size() != 0 {
		t.Fatalf("not compacted: %d", st.Size())
	}
}

func TestCapRefusesThenRecoversAfterDrain(t *testing.T) {
	withMaxBytes(t, 300<<10)
	b, _ := openTmp(t)
	pad := strings.Repeat("y", 30<<10)
	accepted := 0
	for i := 0; i < 20; i++ {
		if b.Push(map[string]any{"i": i, "pad": pad}) == nil {
			accepted++
		}
	}
	if accepted == 20 || b.Dropped != int64(20-accepted) {
		t.Fatalf("accepted=%d dropped=%d", accepted, b.Dropped)
	}
	// ingest comes back: drain everything, compaction frees the file, new events fit again
	for {
		l, next, _ := b.Peek(100)
		if len(l) == 0 {
			break
		}
		b.Ack(next)
	}
	if err := b.Push(map[string]any{"i": 99, "pad": pad}); err != nil {
		t.Fatalf("still full after drain + compaction: %v", err)
	}
}

func TestCapCountsAcceptedBytesNotJustUnacked(t *testing.T) {
	// Acked-but-not-compacted bytes still occupy the file, so they count against the cap.
	withMaxBytes(t, 64<<10)
	b, _ := openTmp(t)
	pad := strings.Repeat("z", 20<<10)
	for i := 0; i < 3; i++ {
		b.Push(map[string]any{"i": i, "pad": pad})
	}
	_, next, _ := b.Peek(10)
	b.Ack(next) // under the 256 KiB compaction threshold: file keeps its size
	if err := b.Push(map[string]any{"i": 3, "pad": pad}); err == nil {
		t.Fatal("cap ignored bytes still on disk")
	}
}
