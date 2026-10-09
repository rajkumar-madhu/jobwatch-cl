package buffer

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCorruptLinesSkippedAndCapEnforced(t *testing.T) {
	p := filepath.Join(t.TempDir(), "b.jsonl")
	b, err := Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	b.Push(map[string]int{"a": 1})
	f, _ := os.OpenFile(p, os.O_APPEND|os.O_WRONLY, 0)
	f.WriteString("{\"torn\":\n")
	f.Close()
	b.Push(map[string]int{"b": 2})
	lines, next, err := b.Peek(10)
	if err != nil || len(lines) != 2 {
		t.Fatalf("lines=%d err=%v", len(lines), err)
	}
	b.Ack(next)

	old := MaxBytes
	MaxBytes = 4 << 10
	defer func() { MaxBytes = old }()
	pad := strings.Repeat("x", 1000)
	var refused int
	for i := 0; i < 10; i++ {
		if b.Push(map[string]string{"p": pad}) != nil {
			refused++
		}
	}
	if refused == 0 || b.Dropped != int64(refused) {
		t.Fatalf("cap not enforced: refused=%d dropped=%d", refused, b.Dropped)
	}
}
