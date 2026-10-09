"""R66: who is on call — rotations, daylight saving, restrictions, layers, overrides, timeline."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from cronsentinel import oncall as O

A, B, C = "ana", "ben", "cy"


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def sched(*layers, tz="UTC", overrides=()):
    return O.Schedule(tz=tz, layers=list(layers), overrides=list(overrides))


def test_daily_rotation_hands_off_at_the_handoff_time():
    s = sched(O.Layer(0, "daily", utc(2026, 10, 5, 9), [A, B, C]))
    assert O.on_call(s, utc(2026, 10, 5, 8, 59))[0] is None            # before the first handoff
    assert O.on_call(s, utc(2026, 10, 5, 9))[0] == A
    assert O.on_call(s, utc(2026, 10, 6, 8, 59))[0] == A                # still A until 09:00 next day
    assert O.on_call(s, utc(2026, 10, 6, 9))[0] == B
    assert O.on_call(s, utc(2026, 10, 8, 9))[0] == A                    # wraps around


def test_weekly_and_custom_rotations():
    w = sched(O.Layer(0, "weekly", utc(2026, 10, 5, 9), [A, B]))         # Monday 09:00
    assert O.on_call(w, utc(2026, 10, 11, 23))[0] == A and O.on_call(w, utc(2026, 10, 12, 9))[0] == B
    c = sched(O.Layer(0, "custom", utc(2026, 10, 5, 0), [A, B, C], shift_hours=12))
    assert [O.on_call(c, utc(2026, 10, 5, h))[0] for h in (0, 11, 12, 23)] == [A, A, B, B]
    assert O.on_call(c, utc(2026, 10, 6, 0))[0] == C


def test_daily_handoff_keeps_local_time_across_daylight_saving():
    """London 09:00 handoff: 08:00 UTC in summer, 09:00 UTC after the clocks go back (25 Oct 2026)."""
    london = ZoneInfo("Europe/London")
    s = sched(O.Layer(0, "daily", datetime(2026, 10, 23, 9, tzinfo=london), [A, B]), tz="Europe/London")
    z = s.zone
    starts = [O.shift_start(s.layers[0], k, z) for k in range(4)]
    assert [x.hour for x in starts] == [8, 8, 9, 9]                      # 23, 24 Oct summer; 25, 26 Oct winter
    assert O.on_call(s, utc(2026, 10, 25, 8, 30))[0] == B                # 08:30 UTC on the 25th is 08:30 local: still Saturday's person


def test_business_hours_layer_over_a_24x7_layer():
    base = O.Layer(0, "weekly", utc(2026, 10, 5, 0), [A], name="24/7")
    office = O.Layer(1, "daily", utc(2026, 10, 5, 0), [B], restrict_days=[0, 1, 2, 3, 4], restrict_start="09:00", restrict_end="17:00", name="office")
    s = sched(base, office)
    assert O.on_call(s, utc(2026, 10, 7, 10)) == (B, "office")           # Wednesday 10:00
    assert O.on_call(s, utc(2026, 10, 7, 18)) == (A, "24/7")             # evening falls through
    assert O.on_call(s, utc(2026, 10, 10, 10)) == (A, "24/7")            # Saturday


def test_overnight_restriction_belongs_to_the_day_it_starts():
    night = O.Layer(0, "daily", utc(2026, 10, 5, 0), [C], restrict_days=[4], restrict_start="18:00", restrict_end="09:00")   # Friday nights
    s = sched(night)
    assert O.on_call(s, utc(2026, 10, 9, 20))[0] == C                    # Friday 20:00
    assert O.on_call(s, utc(2026, 10, 10, 3))[0] == C                    # Saturday 03:00, still Friday's night
    assert O.on_call(s, utc(2026, 10, 10, 20))[0] is None                # Saturday 20:00 is not Friday
    assert O.on_call(s, utc(2026, 10, 9, 12))[0] is None                 # Friday midday


def test_override_beats_every_layer():
    s = sched(O.Layer(0, "daily", utc(2026, 10, 5, 9), [A, B]),
              overrides=[O.Override(C, utc(2026, 10, 6, 18), utc(2026, 10, 6, 22))])
    assert O.on_call(s, utc(2026, 10, 6, 19)) == (C, "override")
    assert O.on_call(s, utc(2026, 10, 6, 22))[0] == B                    # override end is exclusive


def test_timeline_merges_and_splits_at_every_change():
    s = sched(O.Layer(0, "daily", utc(2026, 10, 5, 9), [A, B]),
              overrides=[O.Override(C, utc(2026, 10, 6, 18), utc(2026, 10, 6, 22))])
    segs = O.timeline(s, utc(2026, 10, 5, 0), utc(2026, 10, 7, 12))
    got = [(x["start"].hour, x["start"].day, x["user_id"]) for x in segs]
    assert got == [(0, 5, None), (9, 5, A), (9, 6, B), (18, 6, C), (22, 6, B), (9, 7, A)]
    assert segs[-1]["end"] == utc(2026, 10, 7, 12)


@pytest.mark.parametrize("layer,msg", [
    (O.Layer(0, "monthly", utc(2026, 1, 1), [A]), "rotation"),
    (O.Layer(0, "custom", utc(2026, 1, 1), [A]), "shift_hours"),
    (O.Layer(0, "daily", utc(2026, 1, 1), []), "at least one"),
    (O.Layer(0, "daily", utc(2026, 1, 1), [A], restrict_days=[7]), "weekday"),
    (O.Layer(0, "daily", utc(2026, 1, 1), [A], restrict_start="09:00"), "both"),
    (O.Layer(0, "daily", utc(2026, 1, 1), [A], restrict_start="9am", restrict_end="17:00"), "time like"),
])
def test_validation(layer, msg):
    with pytest.raises(O.InvalidSchedule, match=msg):
        O.validate_layer(layer, "UTC")
