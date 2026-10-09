"""R63: maintenance windows — recurrence (in the window's time zone), scope, validation."""
from datetime import UTC, datetime, timedelta

import pytest

from cronsentinel import maintenance as M


def W(start, end, rrule=None, tz="UTC", scope=None):
    return M.Window(id="w", name="n", scope=scope or {}, starts_at=start, ends_at=end, rrule=rrule, tz=tz)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_one_off():
    w = W(utc(2026, 10, 4, 2), utc(2026, 10, 4, 4))
    assert w.active_at(utc(2026, 10, 4, 3)) and not w.active_at(utc(2026, 10, 4, 5))
    assert w.next_occurrence(utc(2026, 10, 5)) is None


def test_weekly_keeps_recurring_long_after_the_first_occurrence():
    """The R63 bug: only the first occurrence used to count."""
    w = W(utc(2026, 9, 6, 2), utc(2026, 9, 6, 4), "FREQ=WEEKLY;BYDAY=SU")       # Sunday 6 Sep 2026
    assert w.active_at(utc(2026, 10, 4, 3))                                       # four Sundays later
    assert not w.active_at(utc(2026, 10, 5, 3))                                   # Monday
    assert w.next_occurrence(utc(2026, 10, 5)) == (utc(2026, 10, 11, 2), utc(2026, 10, 11, 4))


def test_daily_and_until():
    w = W(utc(2026, 10, 1, 23, 30), utc(2026, 10, 2, 0, 30), "FREQ=DAILY;UNTIL=20261003T233000")
    assert w.active_at(utc(2026, 10, 3, 0, 10))                                   # spans midnight
    assert w.active_at(utc(2026, 10, 3, 23, 45))
    assert not w.active_at(utc(2026, 10, 4, 23, 45))                              # after UNTIL


def test_recurrence_follows_local_time_across_daylight_saving():
    """Sunday 03:00 London: 02:00 UTC in summer time, 03:00 UTC after the clocks go back (25 Oct 2026)."""
    london = M.Window(id="w", name="n", scope={}, starts_at=datetime(2026, 10, 18, 2, 0, tzinfo=UTC),
                      ends_at=datetime(2026, 10, 18, 3, 0, tzinfo=UTC), rrule="FREQ=WEEKLY;BYDAY=SU", tz="Europe/London")
    occ = london.occurrences(utc(2026, 10, 17), utc(2026, 11, 2))
    assert [o[0] for o in occ] == [utc(2026, 10, 18, 2), utc(2026, 10, 25, 3), utc(2026, 11, 1, 3)]


def test_repeated_hour_when_clocks_go_back_uses_the_first():
    """On 25 Oct 2026, 01:30 happens twice in London; the window starts at the first (summer time) one."""
    w = M.Window(id="w", name="n", scope={}, starts_at=datetime(2026, 10, 18, 0, 30, tzinfo=UTC),
                 ends_at=datetime(2026, 10, 18, 1, 30, tzinfo=UTC), rrule="FREQ=WEEKLY;BYDAY=SU", tz="Europe/London")
    assert w.occurrences(utc(2026, 10, 24), utc(2026, 10, 26))[0][0] == utc(2026, 10, 25, 0, 30)


def test_scope():
    assert W(utc(2026, 1, 1), utc(2026, 1, 2)).covers("j1", [], None)                     # {} = every job
    w = W(utc(2026, 1, 1), utc(2026, 1, 2), scope={"tags": ["db"], "job_ids": ["j9"], "environment_ids": ["e1"]})
    assert w.covers("j1", ["db", "x"], None) and w.covers("j9", [], None) and w.covers("j2", [], "e1")
    assert not w.covers("j2", ["web"], "e2")


def test_build_and_validate():
    assert M.build_rrule("weekly", ["su", "mo"], None, "UTC") == "FREQ=WEEKLY;BYDAY=MO,SU"
    assert M.build_rrule("none", [], None, "UTC") is None
    with pytest.raises(M.InvalidWindow, match="weekdays"):
        M.build_rrule("weekly", [], None, "UTC")
    s = utc(2026, 10, 4, 2)
    for args, msg in [((s, s, "UTC", None), "end after"), ((s, s + timedelta(days=15), "UTC", None), "14 days"),
                      ((s, s + timedelta(hours=1), "Mars/Base", None), "time zone"),
                      ((s, s + timedelta(hours=1), "UTC", "FREQ=MONTHLY"), "daily and weekly"),
                      ((s, s + timedelta(hours=25), "UTC", "FREQ=DAILY"), "24 hours")]:
        with pytest.raises(M.InvalidWindow, match=msg):
            M.validate(*args)
