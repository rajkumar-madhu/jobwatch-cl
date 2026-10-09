package agent

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// R54: a job reports metrics by appending to $JOBWATCH_METRICS_FILE; cs-run attaches them to the
// final event and removes the file.
func TestRunAttachesMetricsFromFile(t *testing.T) {
	script := filepath.Join(t.TempDir(), "job.sh")
	os.WriteFile(script, []byte("#!/bin/sh\n"+
		"echo \"size_bytes=7340032\" >> \"$JOBWATCH_METRICS_FILE\"\n"+
		"echo \"rows: 12\" >> \"$JOBWATCH_METRICS_FILE\"\n"+
		"echo \"Bad Name=1\" >> \"$JOBWATCH_METRICS_FILE\"\n"+
		"echo \"$JOBWATCH_METRICS_FILE\" > \"$(dirname \"$0\")/path\"\n"), 0o755)
	var events []Event
	code := Run(context.Background(), Config{LogTailBytes: 1024}, func(e Event) error { events = append(events, e); return nil },
		"tok", "", "", []string{script})
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	last := events[len(events)-1]
	if last.Kind != "success" || last.Metrics["size_bytes"] != 7340032 || last.Metrics["rows"] != 12 || len(last.Metrics) != 2 {
		t.Fatalf("metrics not attached as expected: %+v", last.Metrics)
	}
	p, _ := os.ReadFile(filepath.Join(filepath.Dir(script), "path"))
	if _, err := os.Stat(string(p[:len(p)-1])); !os.IsNotExist(err) {
		t.Fatalf("metrics file was not removed: %v", err)
	}
}

func TestReadMetricsLimitsAndSkipsJunk(t *testing.T) {
	f := filepath.Join(t.TempDir(), "m")
	body := "a=1\nb=x\nc=NaN\nd = 2.5\n"
	for i := 0; i < 30; i++ {
		body += "m" + string(rune('a'+i%26)) + string(rune('a'+i/26)) + "=1\n"
	}
	os.WriteFile(f, []byte(body), 0o600)
	m := ReadMetrics(f)
	if m["a"] != 1 || m["d"] != 2.5 || len(m) != 20 {
		t.Fatalf("got %v", m)
	}
	if _, ok := m["b"]; ok {
		t.Fatal("non-numeric kept")
	}
	if ReadMetrics("") != nil || ReadMetrics(filepath.Join(t.TempDir(), "missing")) != nil {
		t.Fatal("missing file should give nil")
	}
}
