package provider

import "testing"

func TestSameChecksIgnoresSpacing(t *testing.T) {
	if !sameChecks([]string{"metric.size_bytes  >   0", " duration < 2h"}, []string{"metric.size_bytes > 0", "duration < 2h"}) {
		t.Fatal("spacing-only differences should be equal")
	}
	if sameChecks([]string{"metric.size_bytes > 0"}, []string{"metric.size_bytes > 1"}) {
		t.Fatal("different rules must differ")
	}
	if sameChecks([]string{"a > 1"}, []string{"a > 1", "b > 1"}) {
		t.Fatal("different lengths must differ")
	}
}

func TestSameInstant(t *testing.T) {
	if !sameInstant("2026-10-11T02:00:00+05:30", "2026-10-10T20:30:00Z") {
		t.Fatal("same instant in different offsets should be equal")
	}
	if sameInstant("2026-10-11T02:00:00+05:30", "2026-10-11T02:00:00Z") {
		t.Fatal("different instants must differ")
	}
	if sameInstant("not a time", "2026-10-11T02:00:00Z") {
		t.Fatal("unparseable input is never equal")
	}
}

func TestRepeatFromRRule(t *testing.T) {
	rr := "FREQ=WEEKLY;BYDAY=SA,SU"
	if r, d := repeatFromRRule(&rr); r != "weekly" || len(d) != 2 || d[0] != "SA" {
		t.Fatalf("got %s %v", r, d)
	}
	daily := "FREQ=DAILY;UNTIL=20261231T000000"
	if r, d := repeatFromRRule(&daily); r != "daily" || len(d) != 0 {
		t.Fatalf("got %s %v", r, d)
	}
	if r, _ := repeatFromRRule(nil); r != "none" {
		t.Fatalf("got %s", r)
	}
}
