"""Pure rule evaluation logic — no I/O. Tested in tests/test_rules.py."""
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BAD = {"failed", "timeout", "missed"}
CONDITIONS = {"failed", "missed", "late", "runtime_exceeded", "recovered", "consecutive_failures", "sla_breach"}


@dataclass
class JobCtx:
    id: str
    name: str
    tags: list[str]
    environment_id: str | None
    workspace_id: str
    team_id: str | None
    consecutive_failures: int = 0
    success_rate: float | None = None  # over sla_window, 0-100
    sla_target: float | None = None


@dataclass
class Rule:
    id: str
    condition: str
    scope: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    severity: str = "medium"
    channel_ids: list[str] = field(default_factory=list)
    business_hours: dict | None = None
    repeat_interval_s: int | None = None


SCOPE_KEYS = {"job_ids", "tags", "environment_ids", "workspace_ids", "team_ids"}


def validate_rule(condition: str, scope, params, business_hours) -> list[str]:
    """Problems that would make the rule raise inside the engine. Empty list ⇒ valid.

    The engine also isolates each rule, but a rule that can never evaluate is a rule that silently
    never pages, so it is refused at create time instead."""
    errs: list[str] = []
    if condition not in CONDITIONS:
        errs.append(f"condition must be one of {sorted(CONDITIONS)}")
    if not isinstance(scope, dict):
        errs.append("scope must be an object")
    else:
        for k in SCOPE_KEYS & scope.keys():   # other keys are ignored by scope_matches
            v = scope[k]
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                errs.append(f"scope.{k} must be a list of strings")
    if not isinstance(params, dict):
        errs.append("params must be an object")
    elif "count" in params:
        c = params["count"]   # int() in condition_fires; a numeric string from a form field is fine
        if isinstance(c, str) and c.strip().isdigit():
            c = int(c)
        if isinstance(c, bool) or not isinstance(c, int) or not 1 <= c <= 1000:
            errs.append("params.count must be an integer from 1 to 1000")
    if business_hours is not None:
        errs += _validate_business_hours(business_hours)
    return errs


def _validate_business_hours(bh) -> list[str]:
    if not isinstance(bh, dict):
        return ["business_hours must be an object"]
    errs = []
    tz = bh.get("tz", "UTC")
    try:
        if not isinstance(tz, str):
            raise ValueError
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        errs.append(f"business_hours.tz {tz!r} is not a known IANA time zone")
    days = bh.get("days", [1, 2, 3, 4, 5])
    if not isinstance(days, list) or not days or not all(isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 7 for d in days):
        errs.append("business_hours.days must be a non-empty list of 1-7 (Mon=1)")
    for k in ("start", "end"):
        v = bh.get(k)
        if v is None:
            continue
        try:
            if not isinstance(v, str) or len(v) != 5 or v[2] != ":":
                raise ValueError
            time.fromisoformat(v)
        except ValueError:
            errs.append(f"business_hours.{k} must be HH:MM")
    return errs


def scope_matches(rule: Rule, job: JobCtx) -> bool:
    sc = rule.scope or {}
    if not sc:
        return True
    if sc.get("job_ids") and job.id in sc["job_ids"]:
        return True
    if sc.get("tags") and set(sc["tags"]) & set(job.tags):
        return True
    if sc.get("environment_ids") and job.environment_id in sc["environment_ids"]:
        return True
    if sc.get("workspace_ids") and job.workspace_id in sc["workspace_ids"]:
        return True
    if sc.get("team_ids") and job.team_id in sc["team_ids"]:
        return True
    return False


def suppressed_unknown(new_state: str | None, unknown_reason: str | None) -> str | None:
    """R2 — UNKNOWN-visibility suppression. A job we cannot make a statement about must never page.
    Returns a suppression reason (for the ledger) or None.

    This closes a real hole in the old model: when an agent died, every job it owned marched
    HEALTHY -> LATE -> MISSED and fired one alert each, so a single host outage produced a page
    per job. Those jobs are now UNKNOWN/agent_offline and alert once, on the agent, not N times."""
    if new_state != "unknown":
        return None
    return f"unknown:{unknown_reason or 'no_data'}"


def condition_fires(rule: Rule, prev: str, new: str, job: JobCtx, *, new_state: str | None = None,
                    unknown_reason: str | None = None) -> bool:
    if suppressed_unknown(new_state, unknown_reason):
        return False
    c = rule.condition
    if c == "failed":
        return new == "failed"
    if c == "missed":
        return new == "missed"
    if c == "late":
        return new == "late"
    if c == "runtime_exceeded":
        return new == "timeout"
    if c == "recovered":
        return new == "recovered"
    if c == "consecutive_failures":
        return new in ("failed", "timeout") and job.consecutive_failures >= int(rule.params.get("count", 3))
    if c == "sla_breach":
        return job.sla_target is not None and job.success_rate is not None and job.success_rate < job.sla_target
    return False


def in_business_hours(bh: dict | None, now: datetime) -> bool:
    """bh = {"tz": "Asia/Kolkata", "days": [1..5] (Mon=1), "start": "09:00", "end": "18:00"}. None ⇒ always."""
    if not bh:
        return True
    local = now.astimezone(ZoneInfo(bh.get("tz", "UTC")))
    if local.isoweekday() not in bh.get("days", [1, 2, 3, 4, 5]):
        return False
    st = time.fromisoformat(bh.get("start", "00:00")); en = time.fromisoformat(bh.get("end", "23:59"))
    return st <= local.time() <= en


def is_flapping(change_ts: list[datetime], now: datetime, window_s: int = 600, threshold: int = 3) -> bool:
    """D10: ≥threshold status changes in window ⇒ flapping (single 'flapping' alert instead of N)."""
    recent = [t for t in change_ts if (now - t).total_seconds() <= window_s]
    return len(recent) >= threshold


def dedup_key(rule_id: str, job_id: str, condition: str) -> str:
    return f"{rule_id}:{job_id}:{condition}"


def should_repeat(last_sent: datetime | None, repeat_interval_s: int | None, now: datetime) -> bool:
    if last_sent is None:
        return True
    if not repeat_interval_s:
        return False
    return (now - last_sent).total_seconds() >= repeat_interval_s
