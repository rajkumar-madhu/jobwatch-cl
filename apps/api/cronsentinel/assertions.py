"""R54 — run assertions: rules a run must satisfy to count as a success.

A run that exits 0 can still have done the wrong thing — the backup that wrote an empty file, the sync
that processed nothing. Jobs carry assertions; when a run reports success, the processor evaluates
them against the run's metrics and duration, and a broken rule turns the run into a failure with the
reason recorded, so the normal alerting and incident flow takes over.

Grammar (one rule per string, Cronitor-compatible where Cronitor's syntax overlaps):
    metric.<name> <op> <number>        e.g.  metric.size_bytes > 0     metric.rows >= 1000
    duration <op> <timespan>           e.g.  duration < 2h             duration > 30s
    metric.duration <op> <timespan>    (Cronitor's spelling of the same)
  <op> is one of < <= > >= = == !=; <timespan> is a number with an optional unit (ms, s, m/min, h, d).

A metric named in a rule but not reported by the run fails the rule: a backup script that stops
reporting its size is exactly the case this exists to catch.
Cronitor's aggregate form (`metric.x.p95 < 5 over 24 hours`) is not supported and is refused.
"""
import math
import re

MAX_ASSERTIONS = 20
NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_RULE = re.compile(r"^\s*(?:(duration)|metric\.([a-z][a-z0-9_]{0,39}))\s*(<=|>=|==|!=|<|>|=)\s*(.+?)\s*$", re.I)
_SPAN = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60,
         "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600, "d": 86400, "day": 86400, "days": 86400}
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "=": lambda a, b: a == b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


class InvalidAssertion(ValueError):
    pass


def _span_seconds(v: str) -> float:
    m = re.fullmatch(r"([\d.]+)\s*([a-z]*)", v.strip().lower())
    if not m or (m.group(2) and m.group(2) not in _SPAN):
        raise InvalidAssertion(f"'{v}' is not a duration (use e.g. 30s, 15m, 2h)")
    return float(m.group(1)) * _SPAN.get(m.group(2) or "s", 1)


def parse(rule: str) -> tuple[str, str, float]:
    """→ (subject, op, value). subject is 'duration' (value in seconds) or a metric name."""
    if not isinstance(rule, str) or len(rule) > 200:
        raise InvalidAssertion("assertions are strings of at most 200 characters")
    if re.search(r"\bover\b", rule, re.I) or re.match(r"\s*metric\.[a-z0-9_]+\.[a-z0-9]+", rule, re.I):
        raise InvalidAssertion(f"'{rule}': aggregate rules (e.g. '… over 24 hours') are not supported; use a per-run rule")
    m = _RULE.match(rule)
    if not m:
        raise InvalidAssertion(f"'{rule}': expected 'metric.<name> <op> <number>' or 'duration <op> <time>'")
    is_dur, name, op, raw = m.group(1), m.group(2), m.group(3), m.group(4)
    if is_dur or name.lower() == "duration":
        return "duration", op, _span_seconds(raw)
    try:
        val = float(raw)
    except ValueError:
        raise InvalidAssertion(f"'{rule}': '{raw}' is not a number") from None
    if not math.isfinite(val):
        raise InvalidAssertion(f"'{rule}': value must be finite")
    return name.lower(), op, val


def validate(rules: list[str]) -> list[str]:
    """Normalise and check a job's rule list (raises InvalidAssertion)."""
    if len(rules) > MAX_ASSERTIONS:
        raise InvalidAssertion(f"at most {MAX_ASSERTIONS} assertions per job")
    out = []
    for r in rules:
        parse(r)
        out.append(" ".join(r.split()))
    return out


def evaluate(rules: list[str], metrics: dict, duration_ms: int | None) -> list[str]:
    """Return the broken rules, each with what was observed. Empty list = all satisfied."""
    broken = []
    for rule in rules or []:
        try:
            subject, op, want = parse(rule)
        except InvalidAssertion:
            continue                       # validated on write; an old bad row must not block processing
        if subject == "duration":
            if duration_ms is None:
                broken.append(f"{rule} (run duration unknown)")
                continue
            got = duration_ms / 1000
            if not _OPS[op](got, want):
                broken.append(f"{rule} (took {got:g} s)")
            continue
        if subject not in (metrics or {}):
            broken.append(f"{rule} (metric not reported)")
            continue
        got = metrics[subject]
        if not _OPS[op](got, want):
            broken.append(f"{rule} (was {got:g})")
    return broken


def clean_metrics(raw: dict) -> dict:
    """Accept at most 20 finite numeric metrics with simple names; drop the rest silently."""
    out = {}
    for k, v in (raw or {}).items():
        if len(out) >= 20:
            break
        k = str(k).lower()
        if not NAME_RE.fullmatch(k) or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out[k] = f
    return out
