// Package buffer is a crash-safe append-only JSONL queue with a committed offset file.
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

type Buffer struct {
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
	for len(out) < n {
		line, tooLong, complete, err := readLine(r)
		if err == io.EOF && !complete {
			break // no complete line left; a partial tail is a write still in flight — leave it
		}
		if err != nil && err != io.EOF {
			return out, pos, err
		}
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
	if st, err := b.f.Stat(); err == nil && st.Size() == newOffset && st.Size() > 8<<20 {
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

// ---- spool ---------------------------------------------------------------------------------

// SpoolDir is created by install.sh as root:root mode 1733 (sticky, anyone may create, nobody but
// root may list or read others' files).
const spoolMaxFile = 4 * MaxLine

// SpoolWriter writes one execution's events to its own file in the spool directory.
type SpoolWriter struct{ f *os.File }

func OpenSpool(dir, name string) (*SpoolWriter, error) {
	if strings.ContainsAny(name, "/\x00") || name == "" {
		return nil, errors.New("spool: bad name")
	}
	f, err := os.OpenFile(filepath.Join(dir, name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY|syscall.O_NOFOLLOW, 0o600)
	if err != nil {
		return nil, err
	}
	return &SpoolWriter{f: f}, nil
}

func (s *SpoolWriter) Push(v any) error {
	line, err := json.Marshal(v)
	if err != nil {
		return err
	}
	if len(line) > MaxLine {
		return errors.New("spool: line too large")
	}
	_, err = s.f.Write(append(line, '\n'))
	return err
}

func (s *SpoolWriter) Close() error { return s.f.Close() }

// ImportSpool moves complete spool files into the buffer. A file is only imported once its writer
// has finished: wrappers name files <id>.jsonl and the importer skips files modified in the last
// `minAge` (the wrapper emits its terminal event and closes within milliseconds of the run ending;
// long-running jobs keep their file fresh with progress events every 30 s). Returns events imported.
func (b *Buffer) ImportSpool(dir string, isStale func(os.FileInfo) bool, annotate func(raw []byte, uid uint32) []byte) (int, error) {
	ents, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil
		}
		return 0, err
	}
	n := 0
	for _, e := range ents {
		if !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		p := filepath.Join(dir, e.Name())
		fi, err := os.Lstat(p)
		if err != nil || !fi.Mode().IsRegular() { // never follow symlinks or read devices/fifos
			_ = os.Remove(p)
			continue
		}
		if !isStale(fi) {
			continue
		}
		var uid uint32
		if st, ok := fi.Sys().(*syscall.Stat_t); ok {
			uid = st.Uid
		}
		if fi.Size() <= spoolMaxFile {
			f, err := os.OpenFile(p, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
			if err == nil {
				r := bufio.NewReaderSize(f, 64<<10)
				for {
					line, tooLong, complete, err := readLine(r)
					if !tooLong && complete && json.Valid(line.data) {
						if b.PushRaw(annotate(line.data, uid)) == nil {
							n++
						}
					} else if tooLong || (complete && len(bytes.TrimSpace(line.data)) > 0) {
						b.Skipped++
					}
					if err != nil {
						break
					}
				}
				f.Close()
			}
		} else {
			b.Skipped++
		}
		_ = os.Remove(p)
	}
	return n, nil
}
