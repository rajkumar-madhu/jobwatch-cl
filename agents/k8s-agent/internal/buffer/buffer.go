// Package buffer is a crash-safe append-only JSONL queue with a committed offset file.
// R40: copied from linux-agent/internal/buffer (minus the spool, which is host-only) so the K8s
// agent no longer loses its queue on restart or during an ingest outage. Keep the two in sync.
// Chosen over SQLite to keep the agent cgo-free and dependency-free.
//
// R39 changes:
//   - Cross-process locking. `cs-run` wrappers (one process per cron job) append to the same file
//     the daemon compacts. The daemon's "everything acked → truncate" check and the truncate itself
//     were not atomic with respect to other processes, so an event appended in between was lost
//     (and the job then looked missed). Every append, read and compaction now holds flock(2).
//   - Poison lines. A line over the scanner limit made Peek return an error on every call, and a
//     torn line (crash mid-write, or a later append glued onto it) produced invalid JSON that made
//     json.Marshal of the whole batch fail. Either one stopped delivery for good. Invalid and
//     oversized lines are now skipped (and counted), never sent.
//   - Spool. Wrappers running as non-root users cannot open the root-owned buffer. They drop one
//     file per execution into a sticky, write-only spool directory; the daemon imports those.
package buffer

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
)

// MaxLine is the largest event line that will ever be delivered. Log tails are capped well below
// this by the wrapper; anything larger is corruption.
const MaxLine = 1 << 20

// MaxBatchBytes caps one Peek so a batch stays bounded in memory and request size (a 500-line batch
// of up-to-1 MiB lines could OOM-kill the pod). A single line is always returned.
var MaxBatchBytes = 1 << 20

// MaxBytes caps the file while the ingest tier is unreachable (the default PVC is 16 MiB).
// Beyond it new events are refused and counted; the server's slot reconciliation still records
// those runs as missed/unobserved instead of losing them silently.
var MaxBytes int64 = 12 << 20

type Buffer struct {
	Dropped int64
	mu      sync.Mutex
	path    string
	f       *os.File
	offset  int64 // bytes already acked
	Skipped int64 // invalid/oversized lines dropped since start (observability)
}

func Open(path string) (*Buffer, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0o640)
	if err != nil {
		return nil, err
	}
	b := &Buffer{path: path, f: f}
	if raw, err := os.ReadFile(path + ".offset"); err == nil {
		b.offset, _ = strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64)
	}
	if st, err := f.Stat(); err == nil && b.offset > st.Size() {
		b.offset = 0 // file was truncated under us (another daemon instance, manual cleanup)
	}
	return b, nil
}

func (b *Buffer) lock() error   { return syscall.Flock(int(b.f.Fd()), syscall.LOCK_EX) }
func (b *Buffer) unlock() error { return syscall.Flock(int(b.f.Fd()), syscall.LOCK_UN) }

// Push appends one event as a single write under an exclusive lock.
func (b *Buffer) Push(v any) error {
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return b.PushRaw(line)
}

// PushRaw appends an already-encoded JSON line (used by the spool importer).
func (b *Buffer) PushRaw(line []byte) error {
	if len(line) == 0 || len(line) > MaxLine || !json.Valid(line) {
		return errors.New("buffer: refusing invalid or oversized line")
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if err := b.lock(); err != nil {
		return err
	}
	defer b.unlock()
	if st, err := b.f.Stat(); err == nil && st.Size()+int64(len(line))+1 > MaxBytes {
		b.Dropped++
		return errors.New("buffer: full")
	}
	_, err := b.f.Write(append(line, '\n'))
	return err
}

// Peek returns up to n unacked, valid JSON lines and the byte offset after the last line consumed
// (skipped lines are consumed too, so they are acked with the batch and never retried).
func (b *Buffer) Peek(n int) ([]json.RawMessage, int64, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if err := b.lock(); err != nil {
		return nil, b.offset, err
	}
	defer b.unlock()
	rf, err := os.Open(b.path)
	if err != nil {
		return nil, b.offset, err
	}
	defer rf.Close()
	if _, err := rf.Seek(b.offset, io.SeekStart); err != nil {
		return nil, b.offset, err
	}
	r := bufio.NewReaderSize(rf, 64<<10)
	var out []json.RawMessage
	pos := b.offset
	size := 0
	for len(out) < n {
		line, tooLong, complete, err := readLine(r)
		if err == io.EOF && !complete {
			break // no complete line left; a partial tail is a write still in flight — leave it
		}
		if err != nil && err != io.EOF {
			return out, pos, err
		}
		if len(out) > 0 && !tooLong && size+len(line.data) > MaxBatchBytes {
			break // not consumed: it heads the next batch
		}
		size += len(line.data)
		pos += int64(line.consumed)
		if tooLong || len(bytes.TrimSpace(line.data)) == 0 {
			if tooLong {
				b.Skipped++
			}
			continue
		}
		if !json.Valid(line.data) {
			b.Skipped++
			continue
		}
		out = append(out, append(json.RawMessage{}, line.data...))
		if err == io.EOF {
			break
		}
	}
	return out, pos, nil
}

type rawLine struct {
	data     []byte
	consumed int
}

// readLine reads one '\n'-terminated line without ever holding more than MaxLine bytes of it.
// complete is false when EOF arrived before a newline.
func readLine(r *bufio.Reader) (l rawLine, tooLong, complete bool, err error) {
	for {
		chunk, e := r.ReadSlice('\n')
		l.consumed += len(chunk)
		if !tooLong {
			if len(l.data)+len(chunk) > MaxLine+1 {
				tooLong, l.data = true, nil
			} else {
				l.data = append(l.data, chunk...)
			}
		}
		switch {
		case e == nil:
			l.data = bytes.TrimRight(l.data, "\n")
			return l, tooLong, true, nil
		case errors.Is(e, bufio.ErrBufferFull):
			continue
		default:
			return l, tooLong, false, e
		}
	}
}

// Ack commits newOffset and compacts the file when everything is acked and it has grown large.
// Size check and truncate happen under the same exclusive lock writers take, so no append can land
// between them.
func (b *Buffer) Ack(newOffset int64) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if err := b.lock(); err != nil {
		return err
	}
	defer b.unlock()
	b.offset = newOffset
	if st, err := b.f.Stat(); err == nil && st.Size() == newOffset && st.Size() > 256<<10 {
		if err := b.f.Truncate(0); err == nil {
			b.offset = 0
		}
	}
	return writeFileAtomic(b.path+".offset", []byte(strconv.FormatInt(b.offset, 10)), 0o640)
}

func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, perm); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func (b *Buffer) Close() error { return b.f.Close() }
