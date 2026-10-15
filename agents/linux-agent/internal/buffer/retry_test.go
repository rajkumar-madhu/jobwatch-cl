package buffer

// The flusher's retry contract with the buffer: Peek is idempotent until Ack, the committed
// offset survives a restart, compaction only happens once everything is acked, and spool import
// only takes finished files. Uses open() from buffer_test.go.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
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

func ids(t *testing.T, lines []json.RawMessage) []int {
	t.Helper()
	var out []int
	for _, l := range lines {
		var v struct{ I int }
		if err := json.Unmarshal(l, &v); err != nil {
			t.Fatal(err)
		}
		out = append(out, v.I)
	}
	return out
}

func pushN(t *testing.T, b *Buffer, from, n int) {
	t.Helper()
	for i := from; i < from+n; i++ {
		if err := b.Push(map[string]int{"i": i}); err != nil {
			t.Fatal(err)
		}
	}
}

func TestPeekWithoutAckRedeliversTheSameBatch(t *testing.T) {
	b, _ := open(t)
	pushN(t, b, 0, 5)
	l1, n1, _ := b.Peek(3)
	l2, n2, _ := b.Peek(3) // send failed, flusher retries
	if fmt.Sprint(ids(t, l1)) != "[0 1 2]" || fmt.Sprint(ids(t, l2)) != "[0 1 2]" || n1 != n2 {
		t.Fatalf("retry saw %v@%d then %v@%d", ids(t, l1), n1, ids(t, l2), n2)
	}
	b.Ack(n1)
	l3, _, _ := b.Peek(3)
	if fmt.Sprint(ids(t, l3)) != "[3 4]" {
		t.Fatalf("after ack: %v", ids(t, l3))
	}
}

func TestEmptyBufferPeeksNothingAtCurrentOffset(t *testing.T) {
	b, _ := open(t)
	l, next, err := b.Peek(10)
	if err != nil || len(l) != 0 || next != 0 {
		t.Fatalf("l=%v next=%d err=%v", l, next, err)
	}
}

func TestCommittedOffsetSurvivesRestart(t *testing.T) {
	b, p := open(t)
	pushN(t, b, 0, 4)
	_, next, _ := b.Peek(2)
	b.Ack(next)
	b.Close()

	b2, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer b2.Close()
	l, _, _ := b2.Peek(10)
	if fmt.Sprint(ids(t, l)) != "[2 3]" {
		t.Fatalf("after restart got %v, want the unacked [2 3]", ids(t, l))
	}
}

func TestUnackedEventsAreResentAfterRestart(t *testing.T) {
	b, p := open(t)
	pushN(t, b, 0, 3)
	b.Peek(3) // sent, but the agent died before Ack
	b.Close()
	b2, _ := Open(p)
	defer b2.Close()
	if l, _, _ := b2.Peek(10); len(l) != 3 {
		t.Fatalf("got %d, want all 3 resent (at-least-once)", len(l))
	}
}

func TestOffsetPastEndOfFileResetsToStart(t *testing.T) {
	p := filepath.Join(t.TempDir(), "buf.jsonl")
	os.WriteFile(p, []byte(`{"i":7}`+"\n"), 0o640)
	os.WriteFile(p+".offset", []byte("999999"), 0o640)
	b, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	if l, _, _ := b.Peek(10); fmt.Sprint(ids(t, l)) != "[7]" {
		t.Fatalf("stale offset hid events: %v", ids(t, l))
	}
}

func TestGarbageOffsetFileStartsFromZero(t *testing.T) {
	p := filepath.Join(t.TempDir(), "buf.jsonl")
	os.WriteFile(p, []byte(`{"i":1}`+"\n"), 0o640)
	os.WriteFile(p+".offset", []byte("not-a-number"), 0o640)
	b, _ := Open(p)
	defer b.Close()
	if l, _, _ := b.Peek(10); len(l) != 1 {
		t.Fatalf("got %d", len(l))
	}
}

func bigPad() string { return strings.Repeat("p", 512<<10) }

func TestCompactsOnlyWhenEverythingIsAckedAndLarge(t *testing.T) {
	b, p := open(t)
	pad := bigPad()
	for i := 0; i < 18; i++ { // ≈ 9 MiB, over the 8 MiB compaction threshold
		b.Push(map[string]any{"i": i, "pad": pad})
	}
	// drain all but the last event (batches are byte-capped, so this takes several Peeks)
	acked, next := 0, int64(0)
	for acked < 17 {
		l, n, _ := b.Peek(17 - acked)
		if len(l) == 0 {
			t.Fatalf("ran dry after %d", acked)
		}
		acked, next = acked+len(l), n
		if acked < 17 {
			b.Ack(next)
		}
	}
	b.Ack(next) // one event still unacked: must not truncate
	if st, _ := os.Stat(p); st.Size() == 0 {
		t.Fatal("compacted while an event was still unacked")
	}
	l, next, _ := b.Peek(10)
	if fmt.Sprint(ids(t, l)) != "[17]" {
		t.Fatalf("got %v", ids(t, l))
	}
	b.Ack(next)
	if st, _ := os.Stat(p); st.Size() != 0 {
		t.Fatalf("not compacted after full ack: %d bytes", st.Size())
	}
	if off, _ := os.ReadFile(p + ".offset"); string(off) != "0" {
		t.Fatalf("offset file %q after compaction", off)
	}
	pushN(t, b, 100, 1)
	if l, _, _ := b.Peek(10); fmt.Sprint(ids(t, l)) != "[100]" {
		t.Fatalf("after compaction: %v", ids(t, l))
	}
}

func TestSmallFullyAckedFileIsNotCompacted(t *testing.T) {
	b, p := open(t)
	pushN(t, b, 0, 3)
	_, next, _ := b.Peek(10)
	b.Ack(next)
	if st, _ := os.Stat(p); st.Size() != next {
		t.Fatalf("size %d, want %d (no rewrite under the threshold)", st.Size(), next)
	}
}

func TestPushRawRefusesInvalidInput(t *testing.T) {
	b, _ := open(t)
	for _, in := range [][]byte{nil, []byte(""), []byte("{nope"), []byte(`{"x":"` + strings.Repeat("y", MaxLine) + `"}`)} {
		if err := b.PushRaw(in); err == nil {
			t.Errorf("accepted %d-byte invalid line", len(in))
		}
	}
	if l, _, _ := b.Peek(10); len(l) != 0 {
		t.Fatalf("invalid input reached the queue: %d", len(l))
	}
}

func TestSpoolImportSkipsFilesStillBeingWritten(t *testing.T) {
	b, _ := open(t)
	dir := t.TempDir()
	w, _ := OpenSpool(dir, "running")
	w.Push(map[string]string{"kind": "start"})
	n, err := b.ImportSpool(dir, func(os.FileInfo) bool { return false }, func(r []byte, _ uint32) []byte { return r })
	if err != nil || n != 0 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "running.jsonl")); err != nil {
		t.Fatal("in-progress spool file was removed")
	}
	w.Close()
}

func TestSpoolImportDropsOversizedFiles(t *testing.T) {
	b, _ := open(t)
	dir := t.TempDir()
	p := filepath.Join(dir, "huge.jsonl")
	os.WriteFile(p, []byte(strings.Repeat(`{"a":1}`+"\n", spoolMaxFile/8+10)), 0o600)
	n, _ := b.ImportSpool(dir, func(os.FileInfo) bool { return true }, func(r []byte, _ uint32) []byte { return r })
	if n != 0 || b.Skipped != 1 {
		t.Fatalf("n=%d skipped=%d", n, b.Skipped)
	}
	if _, err := os.Stat(p); !os.IsNotExist(err) {
		t.Fatal("oversized spool file left behind")
	}
}

func TestSpoolImportPassesOwnerUIDAndCountsBadLines(t *testing.T) {
	b, _ := open(t)
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "x.jsonl"), []byte(`{"k":1}`+"\n{torn\n"+`{"k":2}`+"\n"+`{"partial":`), 0o600)
	os.WriteFile(filepath.Join(dir, "ignored.txt"), []byte(`{"k":3}`+"\n"), 0o600)
	var uids []uint32
	n, _ := b.ImportSpool(dir, func(os.FileInfo) bool { return true }, func(r []byte, uid uint32) []byte {
		uids = append(uids, uid)
		return r
	})
	if n != 2 || b.Skipped != 1 {
		t.Fatalf("n=%d skipped=%d", n, b.Skipped)
	}
	for _, u := range uids {
		if u != uint32(os.Getuid()) {
			t.Fatalf("annotate got uid %d, file owner is %d", u, os.Getuid())
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "ignored.txt")); err != nil {
		t.Fatal("non-.jsonl file touched")
	}
}

func TestSpoolImportOfMissingDirIsNotAnError(t *testing.T) {
	b, _ := open(t)
	if n, err := b.ImportSpool(filepath.Join(t.TempDir(), "nope"), nil, nil); n != 0 || err != nil {
		t.Fatalf("n=%d err=%v", n, err)
	}
}

// Review #4 (fixed in buffer.go): batches are capped by bytes as well as count, a single line
// over the cap still goes out alone, and nothing is lost across the smaller batches.
func TestBatchIsCappedByBytes(t *testing.T) {
	old := MaxBatchBytes
	MaxBatchBytes = 700 << 10
	defer func() { MaxBatchBytes = old }()
	b, _ := open(t)
	pad := strings.Repeat("q", 300<<10)
	for i := 0; i < 12; i++ {
		b.Push(map[string]any{"i": i, "pad": pad})
	}
	if err := b.Push(map[string]any{"i": 12, "pad": strings.Repeat("Q", 800<<10)}); err != nil { // alone over the cap
		t.Fatal(err)
	}
	var seen []int
	for {
		l, next, _ := b.Peek(100)
		if len(l) == 0 {
			break
		}
		total := 0
		for _, x := range l {
			total += len(x)
		}
		if len(l) > 1 && total > MaxBatchBytes {
			t.Fatalf("batch of %d lines is %d bytes, cap %d", len(l), total, MaxBatchBytes)
		}
		seen = append(seen, ids(t, l)...)
		b.Ack(next)
	}
	if fmt.Sprint(seen) != "[0 1 2 3 4 5 6 7 8 9 10 11 12]" {
		t.Fatalf("delivered %v", seen)
	}
}

// Review #7: the Linux buffer has no size cap (the k8s copy has MaxBytes/Dropped), so a long
// ingest outage or a local user spooling junk can fill /var.
func TestBufferHasASizeCap(t *testing.T) {
	knownBug(t, "review #7", func() error {
		b, p := open(t)
		pad := strings.Repeat("f", 200<<10) // well under MaxLine, so only a size cap can refuse it
		for i := 0; i < 200; i++ { // ≈ 40 MiB, past the 12 MiB the k8s agent allows
			if b.Push(map[string]any{"i": i, "pad": pad}) != nil {
				return nil
			}
		}
		st, _ := os.Stat(p)
		return fmt.Errorf("accepted %.0f MiB without refusing a write", float64(st.Size())/(1<<20))
	})
}
