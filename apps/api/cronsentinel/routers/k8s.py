"""Kubernetes agent ingest + cluster read API."""
import json
from zoneinfo import ZoneInfo
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text

from .. import k8sannotations, pingtokens, schedule
from ..auth import Principal, current_principal
from ..db import system_session, tenant_session
from ..schemas import ClusterOut
from .agents import agent_principal

agent = APIRouter(prefix="/agent/v1/k8s", tags=["agent-k8s"])
router = APIRouter(prefix="/api/v1/clusters", tags=["kubernetes"])


class CronJobSpec(BaseModel):
    UID: str; Namespace: str; Name: str; Schedule: str; ConcurrencyPolicy: str = ""; Image: str = ""; Command: str = ""
    Suspend: bool = False; SuccessfulHistoryLimit: int | None = None; FailedHistoryLimit: int | None = None
    ActiveDeadlineS: int | None = None; StartingDeadlineS: int | None = None; LastScheduleAt: datetime | None = None; LastSuccessAt: datetime | None = None
    TimeZone: str | None = None; Annotations: dict[str, str] | None = None   # R65


def _cluster(s, org, aid):
    c = s.execute(text("SELECT id, name FROM clusters WHERE org_id=:o AND agent_id=:a"), {"o": org, "a": aid}).first()
    if c: return c
    name = s.execute(text("SELECT name FROM agents WHERE id=:a"), {"a": aid}).scalar()
    cid = s.execute(text("INSERT INTO clusters (org_id, agent_id, name) VALUES (:o, :a, :n) ON CONFLICT (org_id, name) DO UPDATE SET agent_id=EXCLUDED.agent_id RETURNING id"), {"o": org, "a": aid, "n": name}).scalar()
    return s.execute(text("SELECT id, name FROM clusters WHERE id=:id"), {"id": cid}).first()


@agent.post("/cronjobs")
def discover_cronjobs(body: dict, a: dict = Depends(agent_principal)):
    org, aid = a["org_id"], a["agent_id"]
    n = 0
    with system_session() as s:
        cl = _cluster(s, org, aid)
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o ORDER BY created_at LIMIT 1"), {"o": org}).scalar()
        for raw in body.get("cronjobs", []):
            cj = CronJobSpec(**raw)
            fp = f"k8s:{cl.id}:{cj.UID}"
            ann = k8sannotations.parse(cj.Annotations)
            tz = "UTC"
            if cj.TimeZone:                      # R65: spec.timeZone (treated as UTC before)
                try:
                    ZoneInfo(cj.TimeZone); tz = cj.TimeZone
                except Exception:
                    ann.warnings.append(f"spec.timeZone '{cj.TimeZone}' is not a known time zone; using UTC")
            valid = schedule.validate(cj.Schedule)
            sched = cj.Schedule if valid else None
            cmd = f"{cj.Image} {cj.Command}".strip()
            job = s.execute(text("SELECT id, name, tags, schedule_expr, tz FROM jobs WHERE org_id=:o AND fingerprint=:f"), {"o": org, "f": fp}).first()
            base_tags = ["kubernetes", cj.Namespace]

            def free(name, own=None):
                hit = s.execute(text("SELECT id FROM jobs WHERE workspace_id=:w AND name=:n"), {"w": ws, "n": name}).first()
                return hit is None or (own is not None and str(hit.id) == str(own))

            if ann.ignore:
                job_id = job.id if job else None
                if job and k8sannotations.IGNORED_TAG not in (job.tags or []):
                    s.execute(text("""UPDATE jobs SET paused=true, status='paused', tags=array_append(tags, :t), updated_at=now() WHERE id=:id"""),
                              {"t": k8sannotations.IGNORED_TAG, "id": job.id})
            elif not job:
                name = f"{cj.Namespace}/{cj.Name}"
                if ann.name:
                    if free(ann.name):
                        name = ann.name
                    else:
                        ann.warnings.append(f"jobwatch.io/name: another job is already called '{ann.name}'; using {name}")
                job_id = s.execute(text(
                    "INSERT INTO jobs (org_id, workspace_id, name, kind, source, fingerprint, heartbeat_token_hash, heartbeat_token_hint, schedule_expr, tz, cluster_id, command, "
                    "status, next_expected_at, paused, tags, grace_s, expected_runtime_s, assertions, description) "
                    "VALUES (:o, :w, :n, 'k8s_cronjob', 'k8s', :f, :th, :thint, :sch, :tz, :cl, :cmd, :st, :nx, :sus, :tags, :g, :rt, :ac, :d) "
                    "ON CONFLICT (workspace_id, name) DO UPDATE SET fingerprint=EXCLUDED.fingerprint RETURNING id"),
                    {"o": org, "w": ws, "n": name, "f": fp, "th": pingtokens.new_token()[1], "thint": "k8s", "sch": sched, "tz": tz, "cl": cl.id, "cmd": cmd,
                     "st": "paused" if cj.Suspend else ("healthy" if valid else "unknown"), "nx": schedule.next_run(cj.Schedule, tz) if valid else None,
                     "sus": cj.Suspend, "tags": base_tags + [x for x in (ann.tags or []) if x not in base_tags],
                     "g": ann.grace_s if ann.grace_s is not None else 600, "rt": ann.expected_runtime_s, "ac": ann.run_checks or [],
                     "d": ann.description}).scalar()
            else:
                job_id = job.id
                was_ignored = k8sannotations.IGNORED_TAG in (job.tags or [])
                changed = sched != job.schedule_expr or tz != (job.tz or "UTC")
                s.execute(text("""UPDATE jobs SET schedule_expr=:sch, tz=:tz, command=:cmd, paused=:sus,
                        status=CASE WHEN :sus THEN 'paused'::job_status WHEN status='paused' THEN 'unknown'::job_status ELSE status END,
                        tags=array_remove(tags, :ig), next_expected_at=:nx, updated_at=now() WHERE id=:id"""),
                          {"sch": sched, "tz": tz, "cmd": cmd, "sus": cj.Suspend, "ig": k8sannotations.IGNORED_TAG,
                           "nx": schedule.next_run(sched, tz) if sched else None, "id": job_id})
                if changed:
                    # slots generated for the old schedule or zone would otherwise be reported missed
                    s.execute(text("DELETE FROM expected_runs WHERE job_id=:id AND state='pending' AND scheduled_for > now()"), {"id": job_id})
                    s.execute(text("UPDATE jobs SET generated_through=now() WHERE id=:id"), {"id": job_id})
                sets, params = [], {"id": job_id}
                if ann.name and ann.name != job.name:
                    if free(ann.name, job_id):
                        sets.append("name=:n"); params["n"] = ann.name
                    else:
                        ann.warnings.append(f"jobwatch.io/name: another job is already called '{ann.name}'; name unchanged")
                if ann.grace_s is not None:
                    sets.append("grace_s=:g"); params["g"] = ann.grace_s
                if ann.expected_runtime_s is not None:
                    sets.append("expected_runtime_s=:rt"); params["rt"] = ann.expected_runtime_s
                if ann.tags is not None:
                    sets.append("tags=:tags"); params["tags"] = base_tags + [x for x in ann.tags if x not in base_tags]
                if ann.run_checks is not None:
                    sets.append("assertions=:ac"); params["ac"] = ann.run_checks
                if ann.description is not None:
                    sets.append("description=:d"); params["d"] = ann.description
                if sets:
                    s.execute(text(f"UPDATE jobs SET {', '.join(sets)} WHERE id=:id"), params)
                if was_ignored and not cj.Suspend:
                    s.execute(text("UPDATE jobs SET paused=false, status='unknown' WHERE id=:id"), {"id": job_id})
            s.execute(text("""
                INSERT INTO k8s_cronjobs (org_id, cluster_id, job_id, namespace, name, uid, schedule, suspend, concurrency_policy, successful_history_limit, failed_history_limit,
                  active_deadline_s, starting_deadline_s, last_schedule_at, last_success_at, image, command, spec, updated_at)
                VALUES (:o, :cl, :j, :ns, :n, :uid, :sch, :sus, :cp, :shl, :fhl, :ad, :sd, :ls, :lsu, :img, :cmd, CAST(:spec AS jsonb), now())
                ON CONFLICT (cluster_id, uid) DO UPDATE SET schedule=EXCLUDED.schedule, suspend=EXCLUDED.suspend, concurrency_policy=EXCLUDED.concurrency_policy,
                  successful_history_limit=EXCLUDED.successful_history_limit, failed_history_limit=EXCLUDED.failed_history_limit, active_deadline_s=EXCLUDED.active_deadline_s,
                  starting_deadline_s=EXCLUDED.starting_deadline_s, last_schedule_at=EXCLUDED.last_schedule_at, last_success_at=EXCLUDED.last_success_at, image=EXCLUDED.image, command=EXCLUDED.command,
                  job_id=EXCLUDED.job_id, updated_at=now()"""),
                {"o": org, "cl": cl.id, "j": job_id, "ns": cj.Namespace, "n": cj.Name, "uid": cj.UID, "sch": cj.Schedule, "sus": cj.Suspend, "cp": cj.ConcurrencyPolicy, "shl": cj.SuccessfulHistoryLimit,
                 "fhl": cj.FailedHistoryLimit, "ad": cj.ActiveDeadlineS, "sd": cj.StartingDeadlineS, "ls": cj.LastScheduleAt, "lsu": cj.LastSuccessAt, "img": cj.Image, "cmd": cj.Command, "spec": json.dumps(raw, default=str)})
            s.execute(text("UPDATE k8s_cronjobs SET annotation_warnings=:w, time_zone=:tz WHERE cluster_id=:cl AND uid=:uid"),
                      {"w": ann.warnings, "tz": cj.TimeZone, "cl": cl.id, "uid": cj.UID})
            n += 1
        s.execute(text("UPDATE agents SET last_seen_at=now() WHERE id=:a"), {"a": aid})
    return {"cronjobs": n}


@agent.post("/events")
async def k8s_events(body: dict, request: Request, a: dict = Depends(agent_principal)):
    org, aid = a["org_id"], a["agent_id"]
    now = datetime.now(UTC); acc = 0
    js = request.app.state.js
    with system_session() as s:
        cl = _cluster(s, org, aid)
        for e in body.get("events", [])[:500]:
            kind = e.get("kind")
            if kind == "k8s_event":
                s.execute(text("INSERT INTO k8s_events (org_id, cluster_id, namespace, object_kind, object_name, reason, message, ts) VALUES (:o, :c, :ns, :ok, :on, :r, :m, :ts)"),
                          {"o": org, "c": cl.id, "ns": e.get("namespace"), "ok": (e.get("meta") or {}).get("object_kind"), "on": e.get("job_name"), "r": e.get("reason"), "m": e.get("stderr_tail"), "ts": e.get("agent_ts") or now})
                acc += 1; continue
            job_id = s.execute(text("SELECT id FROM jobs WHERE org_id=:o AND fingerprint=:f"), {"o": org, "f": f"k8s:{cl.id}:{e.get('cronjob_uid')}"}).scalar()
            if not job_id: continue
            if kind == "progress":  # pod-level failure reason enrichment
                s.execute(text("UPDATE executions SET failure_reason=:r, pod=COALESCE(:pod, pod), node=COALESCE(:node, node) WHERE id=:eid AND org_id=:o"),
                          {"r": e.get("reason"), "pod": e.get("pod"), "node": e.get("node"), "eid": e.get("execution_id"), "o": org})
                s.execute(text("INSERT INTO execution_events (org_id, execution_id, agent_id, sequence, kind, agent_ts, payload) VALUES (:o, :eid, :a, :seq, 'progress', :ts, CAST(:p AS jsonb)) ON CONFLICT DO NOTHING"),
                          {"o": org, "eid": e.get("execution_id"), "a": aid, "seq": e.get("sequence", 5), "ts": e.get("agent_ts"), "p": json.dumps({"reason": e.get("reason"), "pod": e.get("pod"), "node": e.get("node")})})
                acc += 1; continue
            ev = {"org_id": org, "job_id": str(job_id), "agent_id": aid, "kind": kind, "execution_id": e.get("execution_id"), "sequence": e.get("sequence", 0), "agent_ts": e.get("agent_ts"),
                  "server_ts": now.isoformat(), "duration_ms": e.get("duration_ms"), "exit_code": e.get("exit_code"), "host": e.get("node"), "stderr_tail": e.get("stderr_tail"),
                  "meta": {**(e.get("meta") or {}), "pod": e.get("pod"), "reason": e.get("reason"), "k8s_job": e.get("job_name")}}
            if js is not None: await js.publish(f"exec.{org}", json.dumps(ev).encode())
            else:
                from ..processor import process; process(s, ev)
            acc += 1
        s.execute(text("UPDATE agents SET last_seen_at=now() WHERE id=:a"), {"a": aid})
    return {"accepted": acc}


@router.get("", response_model=list[ClusterOut])
def list_clusters(p: Principal = Depends(current_principal)):
    with tenant_session(p.org_id) as s:
        return [dict(r._mapping) for r in s.execute(text("""
            SELECT c.id, c.name, c.scope, c.namespaces, a.last_seen_at, a.version, (SELECT count(*) FROM k8s_cronjobs k WHERE k.cluster_id=c.id) AS cronjobs,
              (SELECT count(*) FROM k8s_cronjobs k JOIN jobs j ON j.id=k.job_id WHERE k.cluster_id=c.id AND j.status IN ('failed','missed','timeout')) AS failing
            FROM clusters c LEFT JOIN agents a ON a.id=c.agent_id ORDER BY c.name""")).all()]


@router.get("/{cluster_id}/cronjobs")
def cluster_cronjobs(cluster_id: UUID, p: Principal = Depends(current_principal), namespace: str | None = None):
    with tenant_session(p.org_id) as s:
        rows = s.execute(text("""
            SELECT k.namespace, k.name, k.schedule, k.suspend, k.concurrency_policy, k.successful_history_limit, k.failed_history_limit, k.active_deadline_s, k.starting_deadline_s,
              k.last_schedule_at, k.last_success_at, k.image, k.annotation_warnings, k.time_zone, j.id AS job_id, j.status::text, j.reliability_score,
              (SELECT count(*) FROM executions e WHERE e.job_id=j.id AND e.status IN ('failed','timeout','missed') AND e.scheduled_ts >= now() - interval '7 days') AS failures_7d,
              (SELECT failure_reason FROM executions e WHERE e.job_id=j.id AND failure_reason IS NOT NULL ORDER BY scheduled_ts DESC LIMIT 1) AS last_reason
            FROM k8s_cronjobs k LEFT JOIN jobs j ON j.id=k.job_id WHERE k.cluster_id=:c AND (CAST(:ns AS text) IS NULL OR k.namespace = CAST(:ns AS text)) ORDER BY k.namespace, k.name"""), {"c": str(cluster_id), "ns": namespace}).all()
        events = s.execute(text("SELECT namespace, object_kind, object_name, reason, message, ts FROM k8s_events WHERE cluster_id=:c ORDER BY ts DESC LIMIT 50"), {"c": str(cluster_id)}).all()
    return {"cronjobs": [dict(r._mapping) for r in rows], "events": [dict(r._mapping) for r in events]}
