"""R55 — alert context: which server, which application, which environment, and why.

An alert used to say "✕ [HIGH] nightly-db-backup: failed". The person paged at 02:20 then had to open
the dashboard to learn where it ran, which environment, and what went wrong. Every alert now carries:

  subject   ✕ [HIGH] FAILED · nightly-db-backup on prod-db-01 (production) — exit 2 after 4m 12s
  details   job, status change, server/cluster, environment, workspace, team, tags, schedule, the run
            that triggered it (exit code, duration, broken run checks, last error line) or, for a
            missed run, the slot that was expected, plus a link to the job in JobWatch

The lookup is one query per alert from ids already in the payload, so every sender (Slack, Teams,
email, Discord, Telegram, webhooks, PagerDuty, Opsgenie) gets the same facts. Output is redacted
before it is shown, and commands are deliberately not included (they can carry secrets in arguments).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from .. import schedule
from ..config import settings
from ..redaction import redact

_ICON = {"failed": "✕", "missed": "⚠", "late": "⚠", "timeout": "⏱", "recovered": "✓", "flapping": "↕"}
_WORD = {"failed": "FAILED", "missed": "MISSED", "late": "LATE", "timeout": "TIMED OUT", "recovered": "RECOVERED",
         "flapping": "FLAPPING", "consecutive_failures": "FAILING REPEATEDLY", "runtime_exceeded": "RUNNING LONG",
         "sla_breach": "BELOW SLA"}
SUBJECT_MAX = 180


def _dur(ms: int | None) -> str | None:
    if ms is None:
        return None
    s = int(ms // 1000)
    if s < 60:
        return f"{s}s" if s else f"{ms} ms"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _local(ts: datetime | None, tz: str) -> str | None:
    if ts is None:
        return None
    try:
        z = ZoneInfo(tz or "UTC")
    except Exception:
        z = ZoneInfo("UTC")
    loc = ts.astimezone(z)
    return f"{loc:%Y-%m-%d %H:%M} {loc.tzname()}"


def build(s, org_id: str, p: dict) -> dict:
    """Facts about the job and run behind this alert. Empty dict for test sends or unknown jobs."""
    jid = p.get("job_id")
    if not jid or p.get("test"):
        return {}
    j = s.execute(text("""
        SELECT j.name, j.kind, j.source, j.tags, j.schedule_expr, j.tz, j.expected_runtime_s, j.last_run_at,
               srv.hostname AS server, cl.name AS cluster, e.name AS environment, w.name AS workspace, t.name AS team
        FROM jobs j
        LEFT JOIN servers srv ON srv.id = j.server_id
        LEFT JOIN clusters cl ON cl.id = j.cluster_id
        LEFT JOIN environments e ON e.id = j.environment_id
        LEFT JOIN workspaces w ON w.id = j.workspace_id
        LEFT JOIN teams t ON t.id = j.team_id
        WHERE j.id = :j AND j.org_id = :o"""), {"j": str(jid), "o": org_id}).first()
    if not j:
        return {}
    ctx: dict = {"job": j.name, "kind": j.kind, "server": j.server, "cluster": j.cluster, "environment": j.environment,
                 "workspace": j.workspace, "team": j.team, "tags": list(j.tags or []), "tz": j.tz or "UTC",
                 "link": f"{settings.web_public_url.rstrip('/')}/jobs/{jid}"}
    if j.schedule_expr:
        ctx["schedule"] = f"{schedule.human(j.schedule_expr)} ({j.schedule_expr}, {j.tz})"
    ns = [t[0:] for t in (j.tags or []) if t not in ("kubernetes", "discovered")]
    if j.kind == "k8s_cronjob" and ns:
        ctx["namespace"] = ns[0]
    eid = p.get("execution_id")
    if eid:
        r = s.execute(text("""SELECT host, pod, node, exit_code, duration_ms, agent_ts_start, agent_ts_end, metrics,
                                     COALESCE(meta->'assertion_failures', '[]'::jsonb) AS checks
                              FROM executions WHERE id = :e AND org_id = :o ORDER BY scheduled_ts DESC LIMIT 1"""),
                      {"e": eid, "o": org_id}).first()
        if r:
            ctx.update(host=r.host or ctx.get("server"), pod=r.pod, node=r.node, exit_code=r.exit_code,
                       duration=_dur(r.duration_ms), started=_local(r.agent_ts_start, ctx["tz"]),
                       finished=_local(r.agent_ts_end, ctx["tz"]), checks=list(r.checks or []),
                       metrics=dict(r.metrics or {}))
            last = s.execute(text("""SELECT content FROM execution_logs WHERE execution_id = :e AND org_id = :o AND stream = 'stderr'
                                     ORDER BY chunk_idx DESC LIMIT 1"""), {"e": eid, "o": org_id}).scalar()
            lines = [ln.strip() for ln in (last or "").splitlines() if ln.strip()]
            if lines:
                ctx["last_error"] = redact(lines[-1])[:240]
    if p.get("condition") in ("missed", "late") or p.get("new_status") in ("missed", "late"):
        slot = s.execute(text("""SELECT scheduled_for FROM expected_runs WHERE job_id = :j AND org_id = :o AND state IN ('missed', 'late', 'pending')
                                 AND scheduled_for <= now() ORDER BY scheduled_for DESC LIMIT 1"""), {"j": str(jid), "o": org_id}).scalar()
        ctx["expected_at"] = _local(slot, ctx["tz"])
    ctx.setdefault("host", ctx.get("server"))
    return {k: v for k, v in ctx.items() if v not in (None, [], {}, "")}


def _where(c: dict) -> str:
    if c.get("cluster"):
        return f"in {c['cluster']}" + (f"/{c['namespace']}" if c.get("namespace") else "")
    return f"on {c['host']}" if c.get("host") else ""


def _reason(p: dict, c: dict) -> str:
    cond = p.get("condition") or p.get("new_status")
    if c.get("checks"):
        return f"run check failed: {c['checks'][0]}"
    if cond in ("missed",):
        return f"no run started for {c['expected_at']}" if c.get("expected_at") else "expected run did not start"
    if cond == "late":
        return f"not started yet for {c['expected_at']}" if c.get("expected_at") else "running late"
    if cond in ("timeout", "runtime_exceeded"):
        return f"still running after {c['duration']}" if c.get("duration") else "running longer than expected"
    if cond == "recovered":
        return f"succeeded in {c['duration']}" if c.get("duration") else "back to normal"
    if c.get("exit_code") is not None:
        return f"exit {c['exit_code']}" + (f" after {c['duration']}" if c.get("duration") else "")
    return ""


def subject(p: dict, c: dict) -> str:
    cond = p.get("condition") or p.get("new_status")
    word = _WORD.get(cond, str(p.get("new_status", "")).upper())
    parts = [f"{_ICON.get(cond, '•')} [{str(p.get('severity', 'medium')).upper()}] {word} · {c.get('job', p.get('job_name'))}"]
    where = _where(c)
    if where:
        parts.append(" " + where)
    if c.get("environment"):
        parts.append(f" ({c['environment']})")
    why = _reason(p, c)
    if why:
        parts.append(f" — {why}")
    out = "".join(parts)
    return out if len(out) <= SUBJECT_MAX else out[: SUBJECT_MAX - 1] + "…"


def details(p: dict, c: dict) -> list[tuple[str, str]]:
    """Ordered (label, value) pairs for the message body; empty values are omitted."""
    rows = [("Job", c.get("job", p.get("job_name"))),
            ("Status", f"{p.get('prev_status')} → {p.get('new_status')}" + (f" ({p['condition']})" if p.get("condition") else "")),
            ("Server", c.get("host") if not c.get("cluster") else None),
            ("Cluster", f"{c['cluster']}" + (f" / {c['namespace']}" if c.get("namespace") else "") if c.get("cluster") else None),
            ("Pod", c.get("pod")), ("Node", c.get("node")),
            ("Environment", c.get("environment")), ("Workspace", c.get("workspace")), ("Team", c.get("team")),
            ("Tags", ", ".join(c["tags"]) if c.get("tags") else None),
            ("Schedule", c.get("schedule")), ("Expected run", c.get("expected_at")),
            ("Started", c.get("started")), ("Finished", c.get("finished")), ("Duration", c.get("duration")),
            ("Exit code", str(c["exit_code"]) if c.get("exit_code") is not None else None),
            ("Failed checks", "; ".join(c["checks"]) if c.get("checks") else None),
            ("Metrics", ", ".join(f"{k}={v:g}" for k, v in c["metrics"].items()) if c.get("metrics") else None),
            ("Last error", c.get("last_error")),
            ("Incident", p.get("incident_id")), ("Run", p.get("execution_id")),
            ("Open in JobWatch", c.get("link"))]
    return [(k, str(v)) for k, v in rows if v not in (None, "", "None")]


def body(p: dict, c: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in details(p, c))
