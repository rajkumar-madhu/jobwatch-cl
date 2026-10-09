"""R4 — outbound exporter. Consumes internal events (jobstatus.*) plus a DB poll for slot
settlements, incident transitions and agent status, builds boundary signals and fans them out
to each enabled destination via Celery. Internal event shape never leaves this process."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text

from .. import events
from ..db import system_session
from ..outbound import schema
from ..outbound.deliver import deliver_signal

log = structlog.get_logger()
POLL_S = 15


def _destinations(s, org_id: str, event_type: str, workspace_id: str | None) -> list[str]:
    rows = s.execute(text("""SELECT id FROM signal_destinations WHERE org_id=:o AND enabled
        AND (event_types = '{}' OR :et = ANY(event_types))
        AND (workspace_ids = '{}' OR CAST(:ws AS uuid) = ANY(workspace_ids))"""),
        {"o": org_id, "et": event_type, "ws": workspace_id}).all()
    return [str(r.id) for r in rows]


def fanout(s, sig: schema.Signal) -> int:
    dests = _destinations(s, sig.org_id, sig.event_type, sig.workspace_id)
    env = sig.envelope()
    for d in dests:
        deliver_signal.delay(sig.org_id, d, env)
    return len(dests)


def _job(s, job_id):
    r = s.execute(text("""SELECT j.name, j.kind::text, j.schedule_expr, j.tz, j.tags, j.reliability_score, j.workspace_id::text,
        e.name AS environment FROM jobs j LEFT JOIN environments e ON e.id=j.environment_id WHERE j.id=:id"""), {"id": job_id}).first()
    return dict(r._mapping) if r else {}


async def consume_jobstatus(js):
    sub = await js.pull_subscribe("jobstatus.>", durable="outbound-exporter", stream=events.STREAM_STATUS)
    while True:
        try:
            msgs = await sub.fetch(50, timeout=5)
        except Exception:
            continue
        for m in msgs:
            try:
                ev = json.loads(m.data)
                with system_session() as s:
                    n = fanout(s, schema.from_jobstatus(ev, _job(s, ev["job_id"])))
                await m.ack()
                if n:
                    log.info("signal fanned out", type="job.state_changed", dests=n)
            except Exception as e:
                log.error("exporter jobstatus failed", error=str(e)); await m.nak()


# ---- polled sources -------------------------------------------------------------------------
# Slots, incidents and agents do not have their own NATS subjects yet; a 15s poll on a
# high-water mark is honest and cheap. Move to subjects when a consumer needs sub-second.
_hwm = {"slot": None, "incident": None, "agent_seen": {}}


def poll_slots(s, since: datetime) -> tuple[int, datetime]:
    rows = s.execute(text("""SELECT er.id, er.org_id::text, er.job_id::text, er.scheduled_for, er.state::text, er.execution_id, er.settled_at,
            e.duration_ms, e.exit_code, e.failure_reason,
            EXTRACT(EPOCH FROM (e.agent_ts_start - er.scheduled_for))::int AS lateness_s
        FROM expected_runs er LEFT JOIN executions e ON e.id = er.execution_id
        WHERE er.settled_at > :since AND er.state IN ('succeeded','failed','missed') ORDER BY er.settled_at LIMIT 500"""), {"since": since}).all()
    n, last = 0, since
    for r in rows:
        n += fanout(s, schema.from_slot(dict(r._mapping), _job(s, r.job_id)))
        last = max(last, r.settled_at)
    return n, last


def poll_incidents(s, since: datetime) -> tuple[int, datetime]:
    # R63: incident_events has `ts`, not `created_at`. The old query failed on every poll, so incident
    # signals never reached destinations; no test ran it against a real database.
    rows = s.execute(text("""SELECT ie.ts AS created_at, ie.kind, i.id::text, i.org_id::text, i.title, i.severity::text, i.status::text,
            i.affected_job_ids::text[] AS affected_job_ids, i.correlation_signals,
            CASE WHEN ie.kind = 'acknowledged' THEN ie.actor_id::text END AS acked_by, i.resolution
        FROM incident_events ie JOIN incidents i ON i.id = ie.incident_id
        WHERE ie.ts > :since AND ie.kind IN ('opened','acknowledged','note','resolved','severity_changed')
        ORDER BY ie.ts LIMIT 500"""), {"since": since}).all()
    kind_map = {"opened": "opened", "resolved": "resolved"}
    n, last = 0, since
    for r in rows:
        d = dict(r._mapping); d["occurred_at"] = r.created_at
        n += fanout(s, schema.from_incident(kind_map.get(r.kind, "updated"), d))
        last = max(last, r.created_at)
    return n, last


def poll_agents(s) -> int:
    """Emits agent.status on transitions only, using each agent's own reported heartbeat interval
    (R3 open item closed: no more fixed 10-minute threshold)."""
    rows = s.execute(text("""SELECT a.id::text, a.org_id::text, a.name, a.host_id AS host, a.kind::text, a.last_seen_at,
               COALESCE(a.heartbeat_interval_s, 60) AS heartbeat_interval_s,   -- R63: was a.host (no such column)
        (SELECT count(*) FROM jobs j JOIN servers sv ON sv.id=j.server_id WHERE sv.agent_id=a.id) AS jobs_affected
        FROM agents a WHERE a.revoked_at IS NULL""")).all()
    now, n = datetime.now(UTC), 0
    for r in rows:
        online = bool(r.last_seen_at) and (now - r.last_seen_at) < timedelta(seconds=r.heartbeat_interval_s * 3)
        prev = _hwm["agent_seen"].get(r.id)
        if prev is None:
            _hwm["agent_seen"][r.id] = online; continue  # first sight: establish baseline, no signal
        if prev != online:
            _hwm["agent_seen"][r.id] = online
            d = dict(r._mapping); d["occurred_at"] = now
            n += fanout(s, schema.from_agent(d, online))
    return n


async def poll_loop():
    now = datetime.now(UTC)
    _hwm["slot"] = _hwm["incident"] = now
    while True:
        try:
            with system_session() as s:
                a, _hwm["slot"] = poll_slots(s, _hwm["slot"])
                b, _hwm["incident"] = poll_incidents(s, _hwm["incident"])
                c = poll_agents(s)
            if a or b or c:
                log.info("polled signals", slots=a, incidents=b, agents=c)
        except Exception as e:
            log.error("exporter poll failed", error=str(e))
        await asyncio.sleep(POLL_S)


AUDIT_STREAM_S = 5


async def audit_stream_loop():
    """R57: push new audit-log events to customer SIEMs (cronsentinel/auditstream.py)."""
    from .. import auditstream

    def _once():
        with system_session() as s:
            return auditstream.tick(s)
    while True:
        try:
            n = await asyncio.to_thread(_once)
            if n:
                log.info("audit events streamed", events=n)
        except Exception as e:
            log.error("audit stream tick failed", error=str(e))
        await asyncio.sleep(AUDIT_STREAM_S)


async def metrics_export_loop():
    """R58: job metrics to Datadog (cronsentinel/metricsexport.py); each export sends once a minute."""
    from .. import metricsexport

    def _once():
        with system_session() as s:
            return metricsexport.tick(s)
    while True:
        try:
            n = await asyncio.to_thread(_once)
            if n:
                log.info("job metrics exported", points=n)
        except Exception as e:
            log.error("metrics export tick failed", error=str(e))
        await asyncio.sleep(15)


async def escalation_loop():
    """R66: move escalations on: page the next step when nobody acknowledged in time."""
    from .. import escalation

    def _once():
        with system_session() as s:
            return escalation.tick(s)
    while True:
        try:
            n = await asyncio.to_thread(_once)
            if n:
                log.info("escalations advanced", runs=n)
        except Exception as e:
            log.error("escalation tick failed", error=str(e))
        await asyncio.sleep(15)


async def main():
    nc, js = await events.connect()
    log.info("outbound-exporter started")
    await asyncio.gather(consume_jobstatus(js), poll_loop(), audit_stream_loop(), metrics_export_loop(), escalation_loop())


if __name__ == "__main__":
    asyncio.run(main())
