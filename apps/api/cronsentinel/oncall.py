"""R66 — who is on call: rotations, restrictions, overrides, and the resulting timeline.

A schedule has layers. Each layer rotates an ordered list of members from a handoff time:
  daily    the next person takes over every day at the handoff's local time
  weekly   every 7 days at the handoff's local time
  custom   every `shift_hours` hours of elapsed time
Daily and weekly rotations count local calendar days, so a 09:00 handoff stays at 09:00 across
daylight-saving changes. A layer can be restricted to some weekdays and a time of day (overnight windows
such as 18:00–09:00 allowed); outside it the layer has nobody. The highest layer with someone on call
wins; an override beats every layer. No layer and no override → nobody (the escalation step skips it).
"""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


class InvalidSchedule(ValueError):
    pass


@dataclass
class Layer:
    position: int
    rotation: str                    # daily | weekly | custom
    handoff_at: datetime             # aware; the first shift starts here
    members: list[str]
    shift_hours: int | None = None
    restrict_days: list[int] | None = None   # 0 = Monday … 6 = Sunday
    restrict_start: str | None = None        # 'HH:MM' local
    restrict_end: str | None = None
    name: str = ""


@dataclass
class Override:
    user_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass
class Schedule:
    tz: str
    layers: list[Layer] = field(default_factory=list)
    overrides: list[Override] = field(default_factory=list)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def validate_layer(layer: Layer, tz: str) -> None:
    try:
        ZoneInfo(tz)
    except Exception:
        raise InvalidSchedule(f"unknown time zone {tz!r}") from None
    if layer.rotation not in ("daily", "weekly", "custom"):
        raise InvalidSchedule("rotation must be daily, weekly or custom")
    if layer.rotation == "custom" and not (layer.shift_hours and 1 <= layer.shift_hours <= 720):
        raise InvalidSchedule("custom rotations need shift_hours between 1 and 720")
    if not layer.members:
        raise InvalidSchedule("a layer needs at least one member")
    if layer.restrict_days is not None and (not layer.restrict_days or any(d not in range(7) for d in layer.restrict_days)):
        raise InvalidSchedule("restrict_days must be weekday numbers 0 (Monday) to 6 (Sunday)")
    if (layer.restrict_start is None) != (layer.restrict_end is None):
        raise InvalidSchedule("give both restrict_start and restrict_end, or neither")
    for v in (layer.restrict_start, layer.restrict_end):
        if v is not None:
            try:
                _hm(v)
            except (ValueError, TypeError):
                raise InvalidSchedule(f"'{v}' is not a time like 09:00") from None
    if layer.restrict_start is not None and layer.restrict_start == layer.restrict_end:
        raise InvalidSchedule("restrict_start and restrict_end must differ")


# ---- one layer -------------------------------------------------------------------------------------

def _shift_index(layer: Layer, at: datetime, z: ZoneInfo) -> int | None:
    """Which shift (0, 1, 2 …) is running at `at`; None before the first handoff."""
    if at < layer.handoff_at:
        return None
    if layer.rotation == "custom":
        return int((at - layer.handoff_at).total_seconds() // (layer.shift_hours * 3600))
    period = 1 if layer.rotation == "daily" else 7
    h_local, a_local = layer.handoff_at.astimezone(z), at.astimezone(z)
    days = (a_local.date() - h_local.date()).days
    if a_local.time() < h_local.time().replace(tzinfo=None):
        days -= 1                         # today's handoff has not happened yet
    return days // period


def shift_start(layer: Layer, k: int, z: ZoneInfo) -> datetime:
    """When shift k begins (UTC)."""
    if layer.rotation == "custom":
        return (layer.handoff_at + timedelta(hours=layer.shift_hours * k)).astimezone(UTC)
    period = 1 if layer.rotation == "daily" else 7
    h_local = layer.handoff_at.astimezone(z)
    d: date = h_local.date() + timedelta(days=period * k)
    return datetime.combine(d, h_local.time().replace(tzinfo=None), tzinfo=z).astimezone(UTC)


def _restricted_out(layer: Layer, at: datetime, z: ZoneInfo) -> bool:
    """True if `at` falls outside the layer's restriction (so the layer has nobody then)."""
    if layer.restrict_days is None and layer.restrict_start is None:
        return False
    loc = at.astimezone(z)
    if layer.restrict_start is None:
        return loc.weekday() not in layer.restrict_days
    st, en, t = _hm(layer.restrict_start), _hm(layer.restrict_end), loc.time().replace(tzinfo=None)
    if st < en:
        inside, day = st <= t < en, loc.weekday()
    else:                                   # overnight window, e.g. 18:00–09:00: belongs to the day it starts
        inside = t >= st or t < en
        day = loc.weekday() if t >= st else (loc - timedelta(days=1)).weekday()
    return not inside or (layer.restrict_days is not None and day not in layer.restrict_days)


def layer_user(layer: Layer, at: datetime, z: ZoneInfo) -> str | None:
    k = _shift_index(layer, at, z)
    if k is None or _restricted_out(layer, at, z):
        return None
    return layer.members[k % len(layer.members)]


# ---- whole schedule ---------------------------------------------------------------------------------

def on_call(sched: Schedule, at: datetime) -> tuple[str | None, str]:
    """(user_id or None, why) — why is 'override' or the layer's name/position."""
    for o in sorted(sched.overrides, key=lambda o: o.starts_at, reverse=True):
        if o.starts_at <= at < o.ends_at:
            return o.user_id, "override"
    z = sched.zone
    for layer in sorted(sched.layers, key=lambda ly: ly.position, reverse=True):
        u = layer_user(layer, at, z)
        if u:
            return u, layer.name or f"layer {layer.position}"
    return None, "nobody"


def timeline(sched: Schedule, start: datetime, end: datetime, max_segments: int = 500) -> list[dict]:
    """Contiguous [start, end) segments with who is on call, merged when unchanged."""
    z = sched.zone
    points = {start, end}
    for layer in sched.layers:
        k0 = _shift_index(layer, start, z)
        k = 0 if k0 is None else k0
        while len(points) < 5000:
            s = shift_start(layer, k, z)
            if s >= end:
                break
            if s > start:
                points.add(s)
            k += 1
        if layer.restrict_days is not None or layer.restrict_start is not None:
            d = start.astimezone(z).date() - timedelta(days=1)
            while True:
                for hm in ([layer.restrict_start, layer.restrict_end] if layer.restrict_start else ["00:00"]):
                    p = datetime.combine(d, _hm(hm), tzinfo=z).astimezone(UTC)
                    if start < p < end:
                        points.add(p)
                d += timedelta(days=1)
                if datetime.combine(d, time(0), tzinfo=z) > end:
                    break
    for o in sched.overrides:
        for p in (o.starts_at, o.ends_at):
            if start < p < end:
                points.add(p)
    pts = sorted(points)
    segs: list[dict] = []
    for a, b in zip(pts, pts[1:]):
        who, why = on_call(sched, a)
        if segs and segs[-1]["user_id"] == who and segs[-1]["source"] == why:
            segs[-1]["end"] = b
        else:
            segs.append({"start": a, "end": b, "user_id": who, "source": why})
        if len(segs) >= max_segments:
            break
    return segs
