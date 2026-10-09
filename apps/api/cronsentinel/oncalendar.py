"""R53 — translate systemd timer schedules to cron, so discovered timers get expected runs.

Inputs seen in practice:
  * the Linux agent's `TimersCalendar` property: "{ OnCalendar=Mon..Fri *-*-* 09:00:00 ; next_elapse=… }"
    (systemd has already normalized the expression); several entries are possible
  * the agent's `TimersMonotonic` property: "{ OnUnitActiveSec=15min ; next_elapse=… }"
  * raw expressions from users or importers: "daily", "Mon..Fri 09:00", "*:0/15", "*-*-01 03:00 UTC"

Translated (checked against `systemd-analyze calendar` in tests):
  shorthands minutely … annually, weekday lists and ranges, `*`, lists, `a..b` ranges, `a/n` and `*/n`
  steps in month, day, hour and minute, seconds of 0, a trailing time zone.
Refused (returned as None with a reason), because cron cannot express them exactly:
  a specific year, non-zero seconds, `~` (days from month end), weekday AND day-of-month both set
  (systemd requires both; cron fires on either), several OnCalendar entries that differ.
Monotonic timers: OnUnitActiveSec / OnUnitInactiveSec become an every-period cron when the period
fits the clock (extra grace = the period, since such timers keep their own phase); OnBootSec and
other one-shot forms have no schedule.
"""
import re
from zoneinfo import ZoneInfo

from . import schedule

_DOW = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
_SHORT = {
    "minutely": "*-*-* *:*:00", "hourly": "*-*-* *:00:00", "daily": "*-*-* 00:00:00",
    "monthly": "*-*-01 00:00:00", "weekly": "Mon *-*-* 00:00:00", "yearly": "*-01-01 00:00:00",
    "annually": "*-01-01 00:00:00", "quarterly": "*-01,04,07,10-01 00:00:00", "semiannually": "*-01,07-01 00:00:00",
}
_PERIOD_CRON = {60: "* * * * *", 120: "*/2 * * * *", 300: "*/5 * * * *", 600: "*/10 * * * *", 900: "*/15 * * * *",
                1200: "*/20 * * * *", 1800: "*/30 * * * *", 3600: "0 * * * *", 7200: "0 */2 * * *",
                10800: "0 */3 * * *", 14400: "0 */4 * * *", 21600: "0 */6 * * *", 28800: "0 */8 * * *",
                43200: "0 */12 * * *", 86400: "0 0 * * *", 604800: "0 0 * * 0"}
_SPAN = {"us": 1e-6, "usec": 1e-6, "ms": 1e-3, "msec": 1e-3, "s": 1, "sec": 1, "second": 1, "seconds": 1,
         "m": 60, "min": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600,
         "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800, "weeks": 604800}
_MAX = {"minute": 59, "hour": 23, "day": 31, "month": 12}


class Untranslatable(ValueError):
    pass


def _field(v: str, kind: str) -> str:
    """systemd component (`*`, `5`, `1,15`, `8..17`, `0/15`, `*/2`, `1..5/2`) → cron field."""
    out = []
    for part in v.split(","):
        if not part:
            raise Untranslatable(f"empty {kind} value")
        base, _, step = part.partition("/")
        if step and not step.isdigit():
            raise Untranslatable(f"{kind} step '{step}'")
        if base == "*":
            rng = "*"
        elif ".." in base:
            a, b = base.split("..", 1)
            if not (a.isdigit() and b.isdigit()):
                raise Untranslatable(f"{kind} range '{base}'")
            rng = f"{int(a)}-{int(b)}"
        elif base.isdigit():
            rng = str(int(base))
            if step:                       # systemd "a/n" = a, a+n, … up to the field maximum
                rng = f"{int(base)}-{_MAX[kind]}"
        else:
            raise Untranslatable(f"{kind} value '{base}'")
        out.append(f"{rng}/{int(step)}" if step else rng)
    return ",".join(out)


def _weekdays(tok: str) -> str:
    days: list[int] = []
    for part in tok.split(","):
        a, sep, b = part.partition("..")
        try:
            x = _DOW[a.lower()[:3]]
            y = _DOW[b.lower()[:3]] if sep else x
        except KeyError:
            raise Untranslatable(f"weekday '{part}'") from None
        order = [1, 2, 3, 4, 5, 6, 0]      # systemd weeks run Mon..Sun and ranges may not wrap
        if order.index(y) < order.index(x):
            raise Untranslatable(f"weekday range '{part}' wraps around the week (systemd rejects it too)")
        days.extend(order[order.index(x): order.index(y) + 1])
    uniq = sorted(set(days))
    return "*" if len(uniq) == 7 else ",".join(map(str, uniq))


def calendar_to_cron(expr: str) -> tuple[str, str | None]:
    """One OnCalendar expression → (cron, tz or None). Raises Untranslatable with a reason."""
    e = expr.strip()
    toks = e.split()
    tz = None
    if toks and not re.search(r"[:\-*]|^(mon|tue|wed|thu|fri|sat|sun)", toks[-1], re.I) and toks[-1].lower() not in _SHORT:
        try:
            ZoneInfo(toks[-1])
            tz = toks.pop()
        except Exception:
            raise Untranslatable(f"unknown part '{toks[-1]}'") from None
    if len(toks) == 1 and toks[0].lower() in _SHORT:
        toks = _SHORT[toks[0].lower()].split()
    dow, date, time_ = "*", "*-*-*", "00:00:00"
    for t in toks:
        if re.match(r"(mon|tue|wed|thu|fri|sat|sun)", t, re.I):
            dow = _weekdays(t)
        elif ":" in t:
            time_ = t
        elif "-" in t or t == "*":
            date = t
        else:
            raise Untranslatable(f"unknown part '{t}'")
    if "~" in date:
        raise Untranslatable("days counted from the end of the month (~)")
    d = date.split("-")
    if len(d) == 3:
        year, month, day = d
        if year not in ("*", ""):
            raise Untranslatable("a specific year")
    elif len(d) == 2:
        month, day = d
    else:
        raise Untranslatable(f"date '{date}'")
    tp = time_.split(":")
    if len(tp) == 2:
        tp.append("00")
    if len(tp) != 3:
        raise Untranslatable(f"time '{time_}'")
    hh, mm, ss = tp
    if ss not in ("0", "00"):
        raise Untranslatable("seconds other than 0 (cron runs on whole minutes)")
    cron_dom, cron_mon = _field(day, "day"), _field(month, "month")
    if dow != "*" and cron_dom != "*":
        raise Untranslatable("weekday and day of month both set (systemd needs both; cron fires on either)")
    cron = f"{_field(mm, 'minute')} {_field(hh, 'hour')} {cron_dom} {cron_mon} {dow}"
    if not schedule.validate(cron):
        raise Untranslatable(f"result '{cron}' is not valid cron")
    return cron, tz


def _timespan_seconds(v: str) -> int | None:
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)\s*([a-z]*)", v.strip().lower()):
        mult = _SPAN.get(unit or "s")
        if mult is None:
            return None
        total += float(num) * mult
    return int(total) if total else None


def translate(spec: str) -> dict:
    """Agent property, raw expression or monotonic timer → {"cron", "tz", "extra_grace_s", "note"}.
    `cron` is None when no cron form exists; `note` then says why."""
    s = (spec or "").strip()
    out = {"cron": None, "tz": None, "extra_grace_s": 0, "note": None}
    if not s:
        out["note"] = "no schedule"
        return out
    cal = re.findall(r"OnCalendar=([^;}]+)", s)
    mono = re.findall(r"On(UnitActive|UnitInactive|Boot|Startup|Active)Sec=([^;}]+)", s)
    if not cal and not mono:
        cal = [s]                          # a bare expression
    if cal:
        crons = []
        try:
            for c in cal:
                crons.append(calendar_to_cron(c))
        except Untranslatable as e:
            out["note"] = f"systemd schedule '{cal[0].strip()}' has no exact cron form: {e}"
            return out
        if len({c for c in crons}) > 1:
            out["note"] = f"timer has {len(crons)} different OnCalendar schedules; cron cannot combine them — set the schedule by hand"
            return out
        out["cron"], out["tz"] = crons[0]
        return out
    kind, val = mono[0]
    if kind not in ("UnitActive", "UnitInactive"):
        out["note"] = f"On{kind}Sec runs once after boot or activation, not on a schedule"
        return out
    period = _timespan_seconds(val)
    cron = _PERIOD_CRON.get(period or 0)
    if not cron:
        out["note"] = f"every {val.strip()} has no cron equivalent; set the schedule by hand"
        return out
    out.update(cron=cron, extra_grace_s=period,
               note=f"interval timer (every {val.strip()}) became '{cron}' with extra grace {period} s; review the schedule")
    return out
