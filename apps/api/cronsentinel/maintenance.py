"""R63 — maintenance windows: one definition used by the alert engine, the reconciler and status pages.

Before R63 the API accepted a recurrence rule (`rrule`) but nothing expanded it: a weekly window only
covered its first occurrence, and from the second week runs inside it were marked missed and alerted.
The engine (Python) and the reconciler (SQL) also matched scope separately.

A window is a start, an end and optionally a recurrence:
  - repeat = none    one occurrence, [starts_at, ends_at]
  - repeat = daily   every day at the same local time, same duration
  - repeat = weekly  on chosen weekdays at the same local time, same duration
Recurrences are expanded in the window's own time zone, so "Sunday 02:00 Europe/London" stays at 02:00
local time across daylight-saving changes. Stored as an RFC 5545 RRULE restricted to DAILY/WEEKLY.

Scope: {} = every job; otherwise any of job_ids, tags, environment_ids matches.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from sqlalchemy import text

MAX_DURATION = timedelta(days=14)
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_MAX_EXPAND = 2000


class InvalidWindow(ValueError):
    pass


@dataclass
class Window:
    id: str
    name: str
    scope: dict
    starts_at: datetime
    ends_at: datetime
    rrule: str | None = None
    tz: str = "UTC"
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> timedelta:
        return self.ends_at - self.starts_at

    def occurrences(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Occurrences overlapping [start, end], as aware UTC datetimes, earliest first."""
        if not self.rrule:
            return [(self.starts_at, self.ends_at)] if self.starts_at <= end and self.ends_at >= start else []
        z = ZoneInfo(self.tz or "UTC")
        local_start = self.starts_at.astimezone(z).replace(tzinfo=None)      # wall-clock time of the first occurrence
        rule = rrulestr(self.rrule, dtstart=local_start)
        lo = (start - self.duration).astimezone(z).replace(tzinfo=None)
        hi = end.astimezone(z).replace(tzinfo=None)
        out = []
        for i, wall in enumerate(rule.between(lo, hi, inc=True)):
            if i >= _MAX_EXPAND:
                break
            s = wall.replace(tzinfo=z).astimezone(UTC)
            e = s + self.duration
            if s <= end and e >= start:
                out.append((s, e))
        return out

    def active_at(self, at: datetime) -> tuple[datetime, datetime] | None:
        occ = self.occurrences(at, at)
        return occ[0] if occ else None

    def next_occurrence(self, after: datetime) -> tuple[datetime, datetime] | None:
        occ = self.occurrences(after, after + timedelta(days=400))
        return occ[0] if occ else None

    def covers(self, job_id: str, tags, environment_id) -> bool:
        sc = self.scope or {}
        if not any(sc.get(k) for k in ("job_ids", "tags", "environment_ids")):
            return True
        return (str(job_id) in {str(x) for x in sc.get("job_ids") or []}
                or bool(set(sc.get("tags") or []) & set(tags or []))
                or (environment_id is not None and str(environment_id) in {str(x) for x in sc.get("environment_ids") or []}))


def build_rrule(repeat: str, weekdays: list[str] | None, until: datetime | None, tz: str) -> str | None:
    if repeat == "none":
        return None
    if repeat not in ("daily", "weekly"):
        raise InvalidWindow("repeat must be none, daily or weekly")
    parts = [f"FREQ={repeat.upper()}"]
    if repeat == "weekly":
        days = [d.upper() for d in (weekdays or [])]
        if not days or any(d not in WEEKDAYS for d in days):
            raise InvalidWindow("weekly windows need weekdays, e.g. [\"SU\"]")
        parts.append("BYDAY=" + ",".join(sorted(set(days), key=WEEKDAYS.index)))
    if until:
        u = until.astimezone(ZoneInfo(tz)).replace(tzinfo=None)
        parts.append("UNTIL=" + u.strftime("%Y%m%dT%H%M%S"))
    return ";".join(parts)


def validate(starts_at: datetime, ends_at: datetime, tz: str, rrule: str | None) -> None:
    if ends_at <= starts_at:
        raise InvalidWindow("the window must end after it starts")
    if ends_at - starts_at > MAX_DURATION:
        raise InvalidWindow("a window can last at most 14 days")
    try:
        ZoneInfo(tz)
    except Exception:
        raise InvalidWindow(f"unknown time zone {tz!r}") from None
    if rrule:
        r = rrule.upper()
        if not (r.startswith("FREQ=DAILY") or r.startswith("FREQ=WEEKLY")):
            raise InvalidWindow("only daily and weekly recurrence is supported")
        try:
            rrulestr(rrule, dtstart=starts_at.replace(tzinfo=None))
        except (ValueError, TypeError):
            raise InvalidWindow("recurrence rule is not valid") from None
        if r.startswith("FREQ=DAILY") and ends_at - starts_at > timedelta(days=1):
            raise InvalidWindow("a daily window can last at most 24 hours")


def load(s, org_id) -> list[Window]:
    rows = s.execute(text("""SELECT id, name, scope, starts_at, ends_at, rrule, tz FROM maintenance_windows
        WHERE org_id = :o AND (rrule IS NOT NULL OR ends_at >= now() - interval '35 days')"""), {"o": str(org_id)}).all()
    return [Window(id=str(r.id), name=r.name, scope=r.scope or {}, starts_at=r.starts_at, ends_at=r.ends_at, rrule=r.rrule, tz=r.tz or "UTC")
            for r in rows]


def get(s, org_id, window_id) -> Window | None:
    """One window by id, whatever its dates (load() skips long-finished one-off windows)."""
    r = s.execute(text("SELECT id, name, scope, starts_at, ends_at, rrule, tz FROM maintenance_windows WHERE org_id = :o AND id = :i"),
                  {"o": str(org_id), "i": str(window_id)}).first()
    return Window(id=str(r.id), name=r.name, scope=r.scope or {}, starts_at=r.starts_at, ends_at=r.ends_at, rrule=r.rrule, tz=r.tz or "UTC") if r else None


def covering(windows: list[Window], job_id, tags, environment_id, at: datetime) -> Window | None:
    for w in windows:
        if w.covers(job_id, tags, environment_id) and w.active_at(at):
            return w
    return None
