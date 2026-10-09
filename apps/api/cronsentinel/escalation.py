"""R66 — escalation: page whoever is on call, and keep paging until someone acknowledges.

An alert rule routed to a channel of kind 'escalation' starts a run for its policy (one active run per
alert and policy; repeats of the same alert do not start another). The first step is paged at once.
Every tick, due runs move on:
  - the incident (or the run itself) was acknowledged → stop, status 'acknowledged'
  - the incident was resolved, or the job recovered → stop, status 'resolved'
  - otherwise page the next step; after the last step start over until `repeat_count` is used up,
    then stop with status 'exhausted'
A step pages the people on call in its schedules and the people it names. Nobody on call in a
schedule is recorded in the history, never silently skipped, and the run still escalates. Pages go
through the policy's delivery channel: email goes to the paged people's addresses; other channels
post the alert naming who is being paged.
"""
import json
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from . import oncall
from .config import settings
from .crypto import decrypt_json

log = structlog.get_logger()
DELIVERY_KINDS = ("email", "slack", "teams", "discord", "telegram", "webhook")


def load_schedule(s, org_id, schedule_id) -> oncall.Schedule | None:
    row = s.execute(text("SELECT tz FROM oncall_schedules WHERE id=:i AND org_id=:o"), {"i": str(schedule_id), "o": str(org_id)}).first()
    if not row:
        return None
    layers = [oncall.Layer(position=r.position, rotation=r.rotation, handoff_at=r.handoff_at, members=[str(m) for m in r.members],
                           shift_hours=r.shift_hours, restrict_days=r.restrict_days, restrict_start=r.restrict_start,
                           restrict_end=r.restrict_end, name=r.name)
              for r in s.execute(text("SELECT * FROM oncall_layers WHERE schedule_id=:i ORDER BY position"), {"i": str(schedule_id)}).all()]
    ovs = [oncall.Override(str(r.user_id), r.starts_at, r.ends_at)
           for r in s.execute(text("SELECT user_id, starts_at, ends_at FROM oncall_overrides WHERE schedule_id=:i AND ends_at > now() - interval '1 day'"),
                              {"i": str(schedule_id)}).all()]
    return oncall.Schedule(tz=row.tz, layers=layers, overrides=ovs)


def resolve(s, org_id, targets: list[dict], at: datetime) -> tuple[list[dict], list[str]]:
    """People to page for a step, and notes for targets with nobody on call."""
    uids, notes = [], []
    for t in targets:
        if t.get("type") == "user":
            uids.append(str(t["id"]))
        elif t.get("type") == "schedule":
            sch = load_schedule(s, org_id, t["id"])
            name = s.execute(text("SELECT name FROM oncall_schedules WHERE id=:i"), {"i": str(t["id"])}).scalar() or "a deleted schedule"
            who = oncall.on_call(sch, at)[0] if sch else None
            if who:
                uids.append(who)
            else:
                notes.append(f"nobody on call in {name}")
    seen, people = set(), []
    for u in uids:
        if u in seen:
            continue
        seen.add(u)
        r = s.execute(text("""SELECT u.id, u.email, u.name FROM users u JOIN memberships m ON m.user_id = u.id
                              WHERE u.id=:u AND m.org_id=:o"""), {"u": u, "o": str(org_id)}).first()
        if r:
            people.append({"id": str(r.id), "email": r.email, "name": r.name or r.email})
        else:
            notes.append("a paged person is no longer in the organization")
    return people, notes


def start(s, org_id, policy_id, payload: dict, dedup_key: str) -> str:
    """Start (or find) the run for this alert and page its first step now."""
    row = s.execute(text("""INSERT INTO escalation_runs (org_id, policy_id, job_id, incident_id, dedup_key, payload, next_at)
        VALUES (:o, :p, :j, :i, :d, CAST(:pl AS jsonb), now())
        ON CONFLICT (org_id, policy_id, dedup_key) WHERE status = 'active' DO NOTHING RETURNING id"""),
        {"o": str(org_id), "p": str(policy_id), "j": payload.get("job_id"), "i": payload.get("incident_id"), "d": dedup_key,
         "pl": json.dumps(payload, default=str)}).first()
    if not row:
        return s.execute(text("""SELECT id FROM escalation_runs WHERE org_id=:o AND policy_id=:p AND dedup_key=:d AND status='active'"""),
                         {"o": str(org_id), "p": str(policy_id), "d": dedup_key}).scalar()
    return str(row.id)


def resolve_for_job(s, org_id, job_id) -> int:
    """The job recovered: stop paging for it."""
    return s.execute(text("""UPDATE escalation_runs SET status='resolved', updated_at=now()
        WHERE org_id=:o AND job_id=:j AND status='active'"""), {"o": str(org_id), "j": str(job_id)}).rowcount


def acknowledge(s, org_id, run_id, user_id) -> bool:
    r = s.execute(text("""UPDATE escalation_runs SET status='acknowledged', acknowledged_by=:u, updated_at=now()
        WHERE id=:r AND org_id=:o AND status='active' RETURNING incident_id"""), {"r": str(run_id), "o": str(org_id), "u": user_id}).first()
    if r and r.incident_id:
        s.execute(text("""UPDATE incidents SET status='acknowledged', acknowledged_at=COALESCE(acknowledged_at, now())
            WHERE id=:i AND status='open'"""), {"i": r.incident_id})
        s.execute(text("INSERT INTO incident_events (org_id, incident_id, kind, actor_id) VALUES (:o, :i, 'acknowledged', :a)"),
                  {"o": str(org_id), "i": r.incident_id, "a": user_id})
    return r is not None


def _deliver(s, org_id, policy, people: list[dict], step_no: int, run_id: str, payload: dict) -> str | None:
    """Send the page; returns an error message or None."""
    from .alerting import context as alert_context
    from .alerting import tasks
    ch = s.execute(text("SELECT kind, config_enc, enabled FROM notification_channels WHERE id=:c AND org_id=:o"),
                   {"c": policy.delivery_channel_id, "o": str(org_id)}).first()
    if not ch or not ch.enabled:
        return "delivery channel missing or disabled"
    if ch.kind not in DELIVERY_KINDS:
        return f"channel kind {ch.kind} cannot deliver pages"
    try:
        p = {**payload, "context": alert_context.build(s, str(org_id), payload)}
    except Exception:
        p = dict(payload)
    names = ", ".join(x["name"] for x in people)
    ack = f"{settings.web_public_url.rstrip('/')}/oncall?ack={run_id}"
    title = f"[Paging {names}] " + alert_context.subject(p, p.get("context") or {})
    body = (f"You are on call for {policy.name} (step {step_no}). Acknowledge to stop escalation: {ack}\n\n"
            + alert_context.body(p, p.get("context") or {}))
    cfg = decrypt_json(ch.config_enc)
    if ch.kind == "email":
        cfg = {**cfg, "to": [x["email"] for x in people]}
    try:
        tasks._SENDERS[ch.kind](cfg, title, body, p) if ch.kind != "email" else tasks._send_email(cfg, title, body)
    except Exception as e:
        log.warning("page delivery failed", run=run_id, error=str(e))
        return "delivery failed"
    return None


def test_page(s, org_id, policy_id, payload: dict) -> str | None:
    """The channel's Send test: page step 1 once, without starting a run."""
    policy = s.execute(text("SELECT id, name, repeat_count, delivery_channel_id FROM escalation_policies WHERE id=:p AND org_id=:o"),
                       {"p": str(policy_id), "o": str(org_id)}).first()
    step = s.execute(text("SELECT targets FROM escalation_steps WHERE policy_id=:p ORDER BY position LIMIT 1"), {"p": str(policy_id)}).first()
    if not policy or not step:
        return "escalation policy missing or has no steps"
    people, notes = resolve(s, org_id, step.targets or [], datetime.now(UTC))
    if not people:
        return "; ".join(notes) or "nobody to page"
    return _deliver(s, org_id, policy, people, 1, "test", payload)


def tick(s, now: datetime | None = None) -> int:
    """Advance every due run. Safe with several workers (rows locked with SKIP LOCKED)."""
    now = now or datetime.now(UTC)
    moved = 0
    for run in s.execute(text("""SELECT r.*, i.status::text AS inc_status FROM escalation_runs r
            LEFT JOIN incidents i ON i.id = r.incident_id
            WHERE r.status='active' AND r.next_at <= :n ORDER BY r.next_at LIMIT 100 FOR UPDATE OF r SKIP LOCKED"""), {"n": now}).all():
        if run.inc_status in ("acknowledged", "resolved"):
            s.execute(text("UPDATE escalation_runs SET status=:st, updated_at=now() WHERE id=:r"),
                      {"st": run.inc_status, "r": run.id})
            continue
        policy = s.execute(text("SELECT id, name, repeat_count, delivery_channel_id FROM escalation_policies WHERE id=:p"), {"p": run.policy_id}).first()
        steps = s.execute(text("SELECT position, delay_min, targets FROM escalation_steps WHERE policy_id=:p ORDER BY position"),
                          {"p": run.policy_id}).all()
        if not policy or not steps:
            s.execute(text("UPDATE escalation_runs SET status='exhausted', updated_at=now() WHERE id=:r"), {"r": run.id})
            continue
        step = steps[min(run.step, len(steps) - 1)]
        people, notes = resolve(s, run.org_id, step.targets or [], now)
        err = _deliver(s, run.org_id, policy, people, run.step + 1, str(run.id), run.payload) if people else None
        entry = {"at": now.isoformat(), "step": run.step + 1, "cycle": run.cycle + 1,
                 "paged": [x["name"] for x in people], "notes": notes + ([err] if err else [])}
        nxt_step, nxt_cycle, status = run.step + 1, run.cycle, "active"
        if nxt_step >= len(steps):
            nxt_step, nxt_cycle = 0, run.cycle + 1
            if nxt_cycle > policy.repeat_count:
                status = "exhausted"
        s.execute(text("""UPDATE escalation_runs SET step=:st, cycle=:cy, status=:status, next_at=:nx,
                          history = history || CAST(:h AS jsonb), updated_at=now() WHERE id=:r"""),
                  {"st": nxt_step, "cy": nxt_cycle, "status": status, "nx": now + timedelta(minutes=step.delay_min),
                   "h": json.dumps([entry]), "r": run.id})
        if run.incident_id:
            s.execute(text("""INSERT INTO incident_events (org_id, incident_id, kind, payload)
                              VALUES (:o, :i, 'notified', CAST(:p AS jsonb))"""),
                      {"o": run.org_id, "i": run.incident_id, "p": json.dumps({"escalation": str(run.id), **entry})})
        moved += 1
    return moved
