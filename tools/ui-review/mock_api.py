"""Mock JobWatch API for UI review — demo data only, no DB."""
import random
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
now = datetime.now(timezone.utc)
iso = lambda d: d.isoformat()
random.seed(7)
# The real API always returns org_id/created_at on these rows; the mock must too, or the
# frontend is developed against a shape production never sends (caught by R14 schema contract).
ORG = str(uuid.uuid5(uuid.NAMESPACE_DNS, "jobwatch-demo-org"))
CREATED = iso(now - timedelta(days=90))
J = [("nightly-database-backup","0 2 * * *","At 02:00 AM","healthy",["prod","db","backup"],91,900),("billing-reconciliation","0 */6 * * *","Every 6 hours","healthy",["prod","billing"],98,300),
     ("market-data-import","*/15 * * * *","Every 15 minutes","recovered",["prod","data"],74,60),("customer-report-generator","30 6 * * *","At 06:30 AM","failed",["prod","reports"],61,600),
     ("log-cleanup","0 4 * * *","At 04:00 AM","healthy",["prod","maintenance"],99,120),("mongodb-backup","0 3 * * *","At 03:00 AM","missed",["prod","db","backup"],83,700),
     ("redis-snapshot","*/30 * * * *","Every 30 minutes","running",["prod","cache"],97,20),("payments/settle-eod","0 22 * * *","At 10:00 PM","late",["kubernetes","payments"],88,400)]
JOBS = [{"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, n)), "workspace_id": "ws", "name": n, "description": None, "kind": "cron", "schedule_expr": c, "schedule_human": h, "tz": "UTC", "expected_runtime_s": e,
         "grace_s": 300, "tags": t, "status": s, "paused": False, "last_run_at": iso(now - timedelta(minutes=random.randint(5, 600))), "last_status": "failed" if s == "failed" else "success",
         "next_expected_at": iso(now + timedelta(hours=random.randint(1, 9))), "reliability_score": r, "heartbeat_token": None, "heartbeat_token_hint": n[-4:],
         "assertions": ["metric.size_bytes > 0", "duration < 2h"] if "backup" in n else []} for n, c, h, s, t, r, e in J]   # R54
def execs(j, n=30):
    out = []; base = j["expected_runtime_s"] * 1000; slow = j["name"] == "nightly-database-backup"
    for i in range(n):
        t = now - timedelta(hours=(n - i) * 3)
        st = "success"; d = int(random.gauss(base, base * .08)); code = 0
        if slow: d = int(base * (1 + 3.2 * i / n))
        if j["status"] == "failed" and i >= n - 3: st, code, d = "failed", 2, 12000
        if j["name"] == "market-data-import" and random.random() < .15: st, code = "failed", 1
        if j["status"] == "missed" and i == n - 1: st, d = "missed", None
        out.append({"id": f"{int(t.timestamp())}-{i}", "job_id": j["id"], "status": st, "scheduled_ts": iso(t), "agent_ts_start": iso(t), "agent_ts_end": iso(t + timedelta(milliseconds=d or 0)), "duration_ms": d, "exit_code": code if d else None, "host": "prod-db-01" if "db" in j["tags"] else "prod-web-01", "skew_ms": random.randint(-40, 40)})
    if j["status"] == "running": out.append({"id": "run-now", "job_id": j["id"], "status": "running", "scheduled_ts": iso(now - timedelta(seconds=25)), "agent_ts_start": iso(now - timedelta(seconds=25)), "agent_ts_end": None, "duration_ms": None, "exit_code": None, "host": "prod-web-01", "skew_ms": 12})
    return list(reversed(out))
EX = {j["id"]: execs(j) for j in JOBS}
byid = {j["id"]: j for j in JOBS}
INC = [{"org_id": ORG, "id": "inc1", "severity": "high", "status": "open", "title": "3 related jobs failing", "started_at": iso(now - timedelta(hours=1, minutes=12)), "acknowledged_at": None, "resolved_at": None, "affected_job_ids": [JOBS[3]["id"], JOBS[5]["id"], JOBS[0]["id"]], "job_names": ["customer-report-generator", "mongodb-backup", "nightly-database-backup"], "root_cause": None, "resolution": None, "last_notified_at": iso(now - timedelta(minutes=58)),
        "correlation_signals": [{"host": "prod-db-01", "job_id": JOBS[3]["id"], "infra": ["io_wait 188ms"]}, {"host": "prod-db-01", "job_id": JOBS[5]["id"]}, {"host": "prod-db-01", "job_id": JOBS[0]["id"], "infra": ["io_wait 188ms", "disk 91%"]}]},
       {"org_id": ORG, "id": "inc2", "severity": "medium", "status": "resolved", "title": "market-data-import is failed", "started_at": iso(now - timedelta(days=1, hours=3)), "acknowledged_at": iso(now - timedelta(days=1, hours=2)), "resolved_at": iso(now - timedelta(days=1)), "affected_job_ids": [JOBS[2]["id"]], "job_names": ["market-data-import"], "root_cause": "Upstream feed returned 502 for 40 minutes", "resolution": "auto: job recovered"}]

@app.get("/api/v1/analytics/overview")
def ov(): return {"total_jobs": 8, "by_status": {"healthy": 3, "failed": 1, "missed": 1, "late": 1, "running": 1, "recovered": 1}, "executions_today": 146, "success_rate_today": 94.52, "top_slowest_7d": [{"name": "nightly-database-backup", "p95_ms": 3120000}, {"name": "mongodb-backup", "p95_ms": 712000}, {"name": "customer-report-generator", "p95_ms": 640000}], "top_failing_7d": [{"name": "customer-report-generator", "failures": 3}, {"name": "market-data-import", "failures": 2}], "mttd_min": 1.4, "mttr_min": 42.5}
@app.get("/api/v1/platform/monitoring-gaps")
# R25 said "two days old" but hardcoded an absolute date (2026-09-20), which drifted outside the
# dashboard's 7-day window and silently zeroed the banner the moment real time passed 2026-09-27.
# R38 caught it as a side effect of running the full suite: compute it relative to `now` instead.
def gaps():
    started = now - timedelta(days=2, hours=1, minutes=8)
    ended = now - timedelta(days=2, hours=0, minutes=26)
    return {"gaps": [{"id": 1, "service": "schedule-generator", "started_at": iso(started), "ended_at": iso(ended), "slots_unobserved": 14}], "unobserved_slots_30d": 14}
@app.get("/api/v1/jobs")
def jobs(status: str | None = None, limit: int = 50): return {"items": [j for j in JOBS if not status or j["status"] == status], "next_cursor": None}
@app.get("/api/v1/jobs/{jid}")
def job(jid: str): return byid[jid]
@app.get("/api/v1/jobs/{jid}/executions")
def jex(jid: str, limit: int = 50): return EX[jid][:limit]
@app.get("/api/v1/jobs/{jid}/next-runs")
def nr(jid: str, n: int = 3): return {"human": byid[jid]["schedule_human"], "runs": [iso(now + timedelta(hours=k * 6 + 2)) for k in range(n)]}
@app.get("/api/v1/jobs/{jid}/impact")
def imp(jid: str):
    if jid == JOBS[0]["id"]: return {"upstream": [], "downstream": [{"id": JOBS[5]["id"], "name": "mongodb-backup", "status": "missed", "depth": 1}, {"id": JOBS[4]["id"], "name": "log-cleanup", "status": "healthy", "depth": 2}]}
    return {"upstream": [{"id": JOBS[0]["id"], "name": "nightly-database-backup", "status": "healthy"}] if jid == JOBS[5]["id"] else [], "downstream": []}
@app.get("/api/v1/executions/{eid}")
def ex(eid: str):
    for jid, xs in EX.items():
        for e in xs:
            if e["id"] == eid:
                t = datetime.fromisoformat(e["scheduled_ts"]); j = byid[jid]
                tl = [{"sequence": 0, "kind": "start", "agent_ts": iso(t), "server_ts": iso(t), "payload": {"command": f"/opt/jobs/{j['name']}.sh"}}, {"sequence": 1, "kind": "progress", "agent_ts": iso(t + timedelta(seconds=30)), "server_ts": iso(t + timedelta(seconds=30)), "payload": {}}]
                if e["agent_ts_end"]: tl.append({"sequence": 2, "kind": "success" if e["status"] == "success" else "fail", "agent_ts": e["agent_ts_end"], "server_ts": e["agent_ts_end"], "payload": {}})
                if "backup" in j["name"]:   # R54: backups report their size; a failed run shows a broken check
                    e = {**e, "metrics": {"size_bytes": 0 if e["status"] == "failed" else 7340032}, "meta": {"assertion_failures": ["metric.size_bytes > 0 (was 0)"] if e["status"] == "failed" else []}}
                return {"execution": e, "timeline": tl, "logs": {"stdout": f"[{t:%H:%M:%S}] starting {j['name']}\n[{t:%H:%M:%S}] connected to postgres\n[{t:%H:%M:%S}] processed 48,211 rows\n", "stderr": 'psycopg2.OperationalError: connection to server at "prod-db-01" failed: timeout expired\n(12 retries exhausted)\n' if e["status"] == "failed" else ""}}
@app.get("/api/v1/executions/{a}/compare/{b}")
def cmp(a: str, b: str):
    fa = next(e for xs in EX.values() for e in xs if e["id"] == a); fb = next(e for xs in EX.values() for e in xs if e["id"] == b)
    return {"a": fa, "b": fb, "delta": {"duration_ms": fb["duration_ms"] - fa["duration_ms"], "pct": round((fb["duration_ms"] - fa["duration_ms"]) / fa["duration_ms"] * 100, 1)} if fa["duration_ms"] and fb["duration_ms"] else None}
@app.get("/api/v1/incidents")
def incs(status: str | None = None, limit: int = 50): return [i for i in INC if not status or i["status"] == status]
@app.get("/api/v1/incidents/{iid}")
def inc(iid: str):
    i = next(x for x in INC if x["id"] == iid)
    return {"incident": i, "timeline": [{"ts": i["started_at"], "kind": "opened", "actor_id": None, "payload": {"prev": "healthy", "new": "failed"}}, {"ts": iso(now - timedelta(hours=1, minutes=10)), "kind": "correlated", "payload": {"reason": "same host prod-db-01", "prev": "healthy", "new": "missed"}}, {"ts": iso(now - timedelta(minutes=58)), "kind": "notified", "payload": {"kind": "slack", "status": "sent"}}, {"ts": iso(now - timedelta(minutes=40)), "kind": "note", "payload": {"text": "DB team looking at io wait on prod-db-01"}}],
            "notifications": [{"sent_at": iso(now - timedelta(minutes=58)), "kind": "slack", "name": "ops-alerts", "status": "sent", "error": None}, {"sent_at": iso(now - timedelta(minutes=58)), "kind": "email", "name": "oncall", "status": "sent", "error": None}],
            "affected_jobs": [{"id": jid, "name": byid[jid]["name"], "status": byid[jid]["status"], "last_run_at": byid[jid]["last_run_at"]} for jid in i["affected_job_ids"]], "executions": EX[i["affected_job_ids"][0]][:4], "downstream_impact": {i["affected_job_ids"][0]: [{"id": JOBS[4]["id"], "name": "log-cleanup", "status": "healthy", "depth": 1}]}}
@app.get("/api/v1/logs/search")
# R16: was `**kw`, which FastAPI reads as a *required* query param named "kw" — every call 422'd
# and the Logs page only ever rendered its error state against this mock. Mirror the real params.
# R38: added `until` and real time-based pagination so the frontend's "Load more" (which walks
# `until` backwards from the oldest row shown) has something real to page through, and generated
# rows span several distinct hours so the hour-grouping headers have more than one bucket to draw.
def logs(q: str | None = None, job_id: str | None = None, stream: str | None = None, host: str | None = None,
         status: str | None = None, since: str | None = None, until: str | None = None, limit: int = 200):
    all_items = []
    for n in range(260):
        j = JOBS[n % 6]
        ts = now - timedelta(minutes=5 * n)  # every 5 min, newest first — spans >20 hours
        failed = n % 17 == 0
        if failed:
            all_items.append({"execution_id": f"log-{n}-err", "stream": "stderr", "content": 'psycopg2.OperationalError: connection to server at "prod-db-01" failed: timeout expired\n(12 retries exhausted)\n', "job_id": j["id"], "job_name": j["name"], "status": "failed", "host": "prod-db-01", "scheduled_ts": iso(ts), "exit_code": 2})
        all_items.append({"execution_id": f"log-{n}", "stream": "stdout", "content": f"starting {j['name']}\nprocessed 48,211 rows in 14.2s\ndone", "job_id": j["id"], "job_name": j["name"], "status": "failed" if failed else "success", "host": "prod-web-01", "scheduled_ts": iso(ts), "exit_code": 2 if failed else 0})
    if since: all_items = [r for r in all_items if r["scheduled_ts"] >= since]
    if until: all_items = [r for r in all_items if r["scheduled_ts"] < until]
    return {"items": all_items[:limit], "hosts": ["prod-web-01", "prod-db-01", "backup-01"]}
# R16: R4's integrations endpoints were never mocked — the Integrations page rendered against 404s
# since R4 and R12's e2e did not notice (its assertions ran before the queries failed).
_DEST = str(uuid.uuid5(uuid.NAMESPACE_DNS, "jobwatch-demo-dest"))
@app.get("/api/v1/integrations/schema")
def sig_schema(): return {"schema": "jobwatch.signal/1", "version": 1, "event_types": ["job.state_changed", "job.failed", "job.missed", "job.recovered"],
                          "headers": ["X-JobWatch-Signal-Id", "X-JobWatch-Schema", "X-JobWatch-Signature"],
                          "signature": "sha256 HMAC of raw body with the destination secret, as 'sha256=<hex>'",
                          "idempotency": "dedupe on signal_id; deliveries may repeat on retry",
                          "example": {"schema": "jobwatch.signal/1", "event_type": "job.failed", "signal_id": "sig_demo"}}
@app.get("/api/v1/integrations/destinations")
def sig_dests(): return [{"id": _DEST, "name": "aegis-prod", "kind": "webhook", "event_types": ["job.failed", "job.missed"], "workspace_ids": None,
                          "enabled": True, "consecutive_failures": 0, "disabled_reason": None, "created_at": CREATED, "sent_24h": 42, "failed_24h": 1,
                          "url": "https://aegis.example.internal/ingest/jobwatch", "has_secret": True}]
@app.get("/api/v1/integrations/deliveries")
def sig_deliveries(limit: int = 100): return [{"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"dlv{i}")), "destination_id": _DEST, "destination": "aegis-prod",
                                               "signal_id": f"sig_{i:04d}", "event_type": "job.failed", "status": "sent" if i else "failed", "attempts": 1 if i else 3,
                                               "last_error": None if i else "connect timeout", "response_code": 202 if i else None,
                                               "created_at": iso(now - timedelta(minutes=5 * i)), "sent_at": iso(now - timedelta(minutes=5 * i)) if i else None}
                                              for i in range(min(limit, 5))]
@app.get("/api/v1/agents")
def agents(): return [{"id": "a1", "kind": "linux", "name": "prod-db-01", "host_id": "4c9e1f…", "version": "0.1.0", "status": "active", "last_seen_at": iso(now - timedelta(seconds=20)), "skew_ms": 38, "revoked_at": None, "created_at": CREATED, "jobs": 3}, {"id": "a2", "kind": "linux", "name": "backup-01", "host_id": "9a02bb…", "version": "0.1.0", "status": "active", "last_seen_at": iso(now - timedelta(minutes=9)), "skew_ms": 7200, "revoked_at": None, "created_at": CREATED, "jobs": 2}, {"id": "a3", "kind": "k8s", "name": "production", "host_id": "k8s:production", "version": "0.1.0", "status": "active", "last_seen_at": iso(now - timedelta(seconds=40)), "skew_ms": -12, "revoked_at": None, "created_at": CREATED, "jobs": 6}]
@app.get("/api/v1/alerts/channels")
def ch(): return [{"id": "c1", "kind": "slack", "name": "ops-alerts", "rate_per_min": 30, "enabled": True, "created_at": CREATED}, {"id": "c2", "kind": "email", "name": "oncall", "rate_per_min": 30, "enabled": True, "created_at": CREATED}]
@app.get("/api/v1/alerts/rules")
def rules(): return [{"org_id": ORG, "created_at": CREATED, "id": "r1", "name": "Prod failures → Slack", "condition": "failed", "scope": {"tags": ["prod"]}, "severity": "high", "enabled": True}, {"org_id": ORG, "created_at": CREATED, "id": "r2", "name": "Missed backups page oncall", "condition": "missed", "scope": {"tags": ["backup"]}, "severity": "critical", "enabled": True}, {"org_id": ORG, "created_at": CREATED, "id": "r3", "name": "Recovery notices", "condition": "recovered", "scope": {}, "severity": "low", "enabled": False}]
@app.get("/api/v1/alerts/ledger")
def ledger(limit: int = 30): return [{"id": k, "incident_id": "inc1", "channel_id": "c1", "kind": "slack", "name": "ops-alerts", "dedup_key": "r1:cust…:failed", "sent_at": iso(now - timedelta(minutes=58 + k * 30)), "status": ["sent", "sent", "rate_limited", "failed"][k % 4], "error": "502 from hooks.slack.com" if k % 4 == 3 else None} for k in range(6)]
@app.get("/api/v1/clusters")
def clusters(): return [{"id": "cl1", "name": "production", "scope": "cluster", "namespaces": [], "last_seen_at": iso(now - timedelta(seconds=40)), "version": "0.1.0", "cronjobs": 6, "failing": 1}, {"id": "cl2", "name": "analytics", "scope": "namespaces", "namespaces": ["analytics", "data"], "last_seen_at": iso(now - timedelta(minutes=3)), "version": "0.1.0", "cronjobs": 4, "failing": 0}]
@app.get("/api/v1/clusters/{cid}/cronjobs")
def cjs(cid: str, namespace: str | None = None):
    rows = [{"namespace": "payments", "name": "settle-eod", "schedule": "0 22 * * *", "suspend": False, "concurrency_policy": "Forbid", "successful_history_limit": 3, "failed_history_limit": 1, "active_deadline_s": 3600, "starting_deadline_s": 200, "last_schedule_at": iso(now - timedelta(hours=2)), "last_success_at": iso(now - timedelta(days=1, hours=2)), "image": "ghcr.io/acme/settle:1.42.0", "job_id": JOBS[7]["id"], "status": "late", "reliability_score": 88, "failures_7d": 1, "last_reason": "OOMKilled"},
            {"namespace": "payments", "name": "reconcile-ledger", "schedule": "*/10 * * * *", "suspend": False, "concurrency_policy": "Replace", "successful_history_limit": 3, "failed_history_limit": 1, "active_deadline_s": None, "starting_deadline_s": None, "last_schedule_at": iso(now - timedelta(minutes=4)), "last_success_at": iso(now - timedelta(minutes=4)), "image": "ghcr.io/acme/ledger:2.1.3", "job_id": JOBS[1]["id"], "status": "healthy", "reliability_score": 99, "failures_7d": 0, "last_reason": None},
            {"namespace": "analytics", "name": "warehouse-sync", "schedule": "0 * * * *", "suspend": True, "concurrency_policy": "Allow", "successful_history_limit": 5, "failed_history_limit": 2, "active_deadline_s": 1800, "starting_deadline_s": None, "last_schedule_at": iso(now - timedelta(days=3)), "last_success_at": iso(now - timedelta(days=3)), "image": "ghcr.io/acme/wh:0.9", "job_id": JOBS[4]["id"], "status": "paused", "reliability_score": 96, "failures_7d": 0, "last_reason": None}]
    # R65: time zone, annotation warnings, and an ignored (not monitored) CronJob
    rows[0] = {**rows[0], "time_zone": "Asia/Kolkata", "annotation_warnings": []}
    rows[1] = {**rows[1], "time_zone": None, "annotation_warnings": ["jobwatch.io/grace: 'soon' is not a duration (e.g. 90, 10m, 2h, 1h30m)"]}
    rows.append({**rows[1], "name": "scratch-cleanup", "job_id": None, "status": None, "time_zone": None, "annotation_warnings": []})
    return {"cronjobs": [r for r in rows if not namespace or r["namespace"] == namespace], "events": [{"namespace": "payments", "object_kind": "Pod", "object_name": "settle-eod-29012345-x8k2p", "reason": "OOMKilled", "message": "Container settle was OOM killed (limit 512Mi)", "ts": iso(now - timedelta(hours=2))}, {"namespace": "payments", "object_kind": "Pod", "object_name": "settle-eod-29012345-x8k2p", "reason": "BackOff", "message": "Back-off restarting failed container", "ts": iso(now - timedelta(hours=1, minutes=55))}]}
@app.get("/api/v1/topology")
def topo(): return {"status": "failed", "servers": [{"id": "s1", "name": "prod-db-01", "status": "failed", "users": [{"name": "root", "status": "failed", "jobs": [{"id": JOBS[0]["id"], "name": "nightly-database-backup", "status": "healthy"}, {"id": JOBS[3]["id"], "name": "customer-report-generator", "status": "failed"}]}, {"name": "postgres", "status": "missed", "jobs": [{"id": JOBS[5]["id"], "name": "mongodb-backup", "status": "missed"}]}]}, {"id": "s2", "name": "prod-web-01", "status": "healthy", "users": [{"name": "www-data", "status": "healthy", "jobs": [{"id": JOBS[1]["id"], "name": "billing-reconciliation", "status": "healthy"}, {"id": JOBS[4]["id"], "name": "log-cleanup", "status": "healthy"}]}]}], "clusters": [{"id": "cl1", "name": "production", "status": "late", "namespaces": [{"name": "payments", "status": "late", "cronjobs": [{"id": JOBS[7]["id"], "name": "settle-eod", "status": "late"}, {"id": JOBS[1]["id"], "name": "reconcile-ledger", "status": "healthy"}]}]}], "heartbeat_only": [{"id": JOBS[2]["id"], "name": "market-data-import", "status": "recovered"}, {"id": JOBS[6]["id"], "name": "redis-snapshot", "status": "running"}]}
@app.get("/api/v1/dependencies")
def deps(): return {"nodes": [{"id": JOBS[0]["id"], "name": "nightly-database-backup", "status": "healthy"}, {"id": JOBS[5]["id"], "name": "mongodb-backup", "status": "missed"}, {"id": JOBS[4]["id"], "name": "log-cleanup", "status": "healthy"}, {"id": JOBS[3]["id"], "name": "customer-report-generator", "status": "failed"}], "edges": [{"job_id": JOBS[5]["id"], "depends_on_job_id": JOBS[0]["id"]}, {"job_id": JOBS[4]["id"], "depends_on_job_id": JOBS[5]["id"]}, {"job_id": JOBS[3]["id"], "depends_on_job_id": JOBS[0]["id"]}]}
@app.get("/api/v1/analytics/series")
def series(days: int = 14): return {"bucket": "day", "points": [{"t": iso(now - timedelta(days=days - k)), "executions": 140 + random.randint(-10, 10), "ok": 132 + random.randint(-8, 4), "failed": random.randint(0, 6), "missed": random.randint(0, 2), "p50_ms": 42000 + k * 900, "p95_ms": 380000 + k * 9000} for k in range(days)], "incidents": [{"t": iso(now - timedelta(days=days - k)), "n": random.choice([0, 0, 1, 2])} for k in range(days)]}
@app.get("/api/v1/analytics/jobs")
def ajobs(days: int = 30, sort: str = "reliability"): return sorted([{**j, "runs": len(EX[j["id"]]), "ok": sum(e["status"] == "success" for e in EX[j["id"]]), "failures": sum(e["status"] == "failed" for e in EX[j["id"]]), "missed": sum(e["status"] == "missed" for e in EX[j["id"]]), "success_rate": round(100 * sum(e["status"] == "success" for e in EX[j["id"]]) / len(EX[j["id"]]), 1), "p50_ms": j["expected_runtime_s"] * 1000, "p95_ms": int(j["expected_runtime_s"] * 1400), "drift_pct": 212.0 if j["name"] == "nightly-database-backup" else round(random.uniform(-4, 9), 1), "sla_target": 99.5 if "backup" in j["tags"] else None, "sla_met": False if j["status"] in ("failed", "missed") else (True if "backup" in j["tags"] else None), "est_cost_usd": round(j["expected_runtime_s"] / 3600 * len(EX[j["id"]]) * .05, 2)} for j in JOBS], key=lambda x: x["reliability_score"])
@app.get("/api/v1/analytics/mttr")
def mttr(days: int = 30): return {"incidents": 7, "mttd_min": 1.4, "mtta_min": 9.0, "mttr_min": 42.5, "resolved": 6}
@app.get("/api/v1/analytics/report")
def rep(period: str = "weekly"): return {"period": period, "runs": 1012, "success_rate": 96.1, "success_rate_prev": 97.4, "delta_pts": -1.3, "failed": 27, "missed": 6, "p95_ms": 402000, "incidents": 4, "open_incidents": 1, "jobs_with_sla": 2, "worst_jobs": [{"name": "customer-report-generator", "failures": 9}, {"name": "market-data-import", "failures": 8}, {"name": "mongodb-backup", "failures": 3}], "slowest_jobs": [{"name": "nightly-database-backup", "p95_ms": 3120000}, {"name": "mongodb-backup", "p95_ms": 712000}], "lowest_reliability": [{"name": "customer-report-generator", "reliability_score": 61}, {"name": "market-data-import", "reliability_score": 74}, {"name": "mongodb-backup", "reliability_score": 83}]}
@app.get("/api/v1/copilot/suggestions")
def sug(): return {"questions": ["Why did the most recent failure happen?", "Which jobs failed after the last deployment?", "Which jobs have increasing duration?", "Which servers have the most cron failures?"]}
@app.get("/api/v1/copilot/history")
def hist(limit: int = 10): return [{"id": "h1", "question": "Why is customer-report-generator failed?", "created_at": iso(now - timedelta(minutes=35)), "answer": ANS}]
ANS = {"summary": "customer-report-generator has failed 3 consecutive runs since 06:30 because it cannot obtain a PostgreSQL connection from prod-db-01.", "root_cause": "Database connection timeout — prod-db-01 disk I/O latency climbed from 12 ms to 188 ms, starving the connection pool while nightly-database-backup ran 3× longer than usual.", "evidence": ["12 'connection to server at prod-db-01 failed: timeout expired' lines across the last 3 runs", "io_wait on prod-db-01: 12 ms → 188 ms over 48 h; disk 91%", "nightly-database-backup duration +212% in the same window", "mongodb-backup missed its 03:00 schedule on the same host"], "affected_resources": ["prod-db-01", "customer-report-generator", "mongodb-backup", "nightly-database-backup"], "confidence": 0.82, "remediation": ["Check PostgreSQL connection pool saturation (pg_stat_activity)", "Inspect disk utilisation and I/O wait on prod-db-01; free space below 10%", "Consider moving nightly-database-backup earlier or to backup-01 to avoid overlap with 06:30 report"], "relevant_logs": ["psycopg2.OperationalError: connection to server at \"prod-db-01\" failed: timeout expired", "(12 retries exhausted)"], "investigate_commands": ["ssh prod-db-01 'iostat -x 5 3'", "psql -h prod-db-01 -c \"select state, count(*) from pg_stat_activity group by 1\"", "df -h /var/lib/postgresql"], "what_changed": ["Disk usage on prod-db-01 crossed 90% ~36 h ago", "Backup duration began climbing 5 days ago"]}
@app.post("/api/v1/copilot/ask")
def ask(body: dict): return {"id": "x", "answer": ANS, "context": {"jobs": ["customer-report-generator", "mongodb-backup", "nightly-database-backup"], "executions": 36, "metrics_points": 48}, "model": "qwen2.5:14b", "latency_ms": 4210}
@app.get("/api/v1/billing")
def bill(): return {"subscription": {"plan": "team", "status": "trialing", "trial_ends_at": iso(now + timedelta(days=9)), "current_period_end": None, "cancel_at_period_end": False, "has_customer": False, "effective_plan": "team"}, "plans": [{"plan": "free", "max_jobs": 5, "retention_days": 7, "features": ["email"], "monthly_usd": 0}, {"plan": "developer", "max_jobs": 50, "retention_days": 30, "features": ["email", "slack", "webhook"], "monthly_usd": 19}, {"plan": "team", "max_jobs": 500, "retention_days": 90, "features": ["slack", "webhook", "teams", "ai", "kubernetes"], "monthly_usd": 79}, {"plan": "business", "max_jobs": 5000, "retention_days": 365, "features": ["pagerduty", "sso", "analytics"], "monthly_usd": 299}, {"plan": "enterprise", "max_jobs": None, "retention_days": None, "features": ["all"], "monthly_usd": None}], "usage": {"jobs": 8, "executions_this_month": 4120, "log_bytes": 18400000}, "limits": {"max_jobs": 500, "retention_days": 90}, "configured": False}
@app.get("/public/status/{slug}")
def st(slug: str): return {"title": "Acme scheduled jobs", "overall": "degraded", "jobs": [{"name": j["name"], "status": j["status"], "last_run_at": j["last_run_at"], "uptime_90d": round(99.9 - (100 - j["reliability_score"]) / 4, 2), "recent": [e["status"] for e in EX[j["id"]]][:30], "show_duration": False} for j in JOBS[:5]], "incidents": [{"title": INC[1]["title"], "severity": "medium", "status": "resolved", "started_at": INC[1]["started_at"], "resolved_at": INC[1]["resolved_at"]}], "maintenance": [{"starts_at": iso(now + timedelta(days=2)), "ends_at": iso(now + timedelta(days=2, hours=2))}]}
@app.get("/api/v1/workspaces")
def ws(): return [{"id": "ws", "name": "default", "created_at": CREATED}]
@app.get("/api/v1/status-pages")
def sp(): return [{"id": "p1", "slug": "demo", "title": "Acme scheduled jobs", "visibility": "public", "job_ids": [j["id"] for j in JOBS[:5]]}]
@app.get("/auth/providers")   # R51
def providers(): return {"social": [{"alias": "google", "label": "Google"}, {"alias": "github", "label": "GitHub"}, {"alias": "microsoft", "label": "Microsoft"}], "sso": True, "signup": True}
@app.get("/auth/discover")   # R51
def discover(email: str): return {"sso": email.endswith("@acme.com"), "provider": "Okta" if email.endswith("@acme.com") else None}
@app.get("/auth/session")
def sess(): return {"user": {"id": "u", "email": "le@finspot.in", "name": "LE"}, "org_id": "o", "orgs": [{"id": "o", "name": "Acme", "slug": "acme", "plan": "team", "role": "owner"}]}
# R66: on-call
_PEOPLE = [{"id": "7a000000-0000-4000-8000-000000000001", "name": "Ana Rao", "email": "ana@acme.io"},
           {"id": "7a000000-0000-4000-8000-000000000002", "name": "Ben Ito", "email": "ben@acme.io"},
           {"id": "7a000000-0000-4000-8000-000000000003", "name": "Cy Diaz", "email": "cy@acme.io"}]
def _sched(sid, name, tz, layers, now_name, src="Weekly"):
    tl = []
    t0 = now.replace(minute=0, second=0, microsecond=0)
    order = [p["name"] for p in _PEOPLE]
    for i in range(6):
        nm = order[i % 3]
        tl.append({"start": iso(t0 + timedelta(days=7 * i)), "end": iso(t0 + timedelta(days=7 * (i + 1))), "user_id": _PEOPLE[i % 3]["id"], "name": nm,
                   "source": "override" if (sid.endswith("1") and i == 1) else src})
    return {"id": sid, "name": name, "tz": tz, "description": None, "layers": layers,
            "on_call_now": {"user_id": _PEOPLE[0]["id"], "name": now_name, "source": src}, "timeline": tl,
            "overrides": [{"id": "ov1", "user_id": _PEOPLE[2]["id"], "starts_at": iso(now + timedelta(days=7)), "ends_at": iso(now + timedelta(days=8))}]}
_SCHEDS = [_sched("5c000000-0000-4000-8000-0000000000a1", "Platform primary", "Asia/Kolkata",
                  [{"name": "Weekly", "rotation": "weekly", "shift_hours": None, "members": [{"id": p["id"], "name": p["name"]} for p in _PEOPLE]}], "Ana Rao"),
           _sched("5c000000-0000-4000-8000-0000000000a2", "Data team business hours", "Europe/London",
                  [{"name": "Office", "rotation": "daily", "shift_hours": None, "members": [{"id": p["id"], "name": p["name"]} for p in _PEOPLE[1:]]}], "Ben Ito", "Office")]
@app.get("/api/v1/oncall/people")
def oc_people(): return _PEOPLE
@app.get("/api/v1/oncall/schedules")
def oc_scheds(): return [{k: v for k, v in s.items() if k not in ("timeline", "overrides")} for s in _SCHEDS]
@app.get("/api/v1/oncall/schedules/{sid}")
def oc_sched(sid: str): return next(s for s in _SCHEDS if s["id"] == sid)
@app.get("/api/v1/oncall/policies")
def oc_policies(): return [{"id": "5d000000-0000-4000-8000-0000000000b1", "name": "Platform", "repeat_count": 1, "delivery_channel_id": "ch-slack",
    "steps": [{"delay_min": 15, "targets": [{"type": "schedule", "id": _SCHEDS[0]["id"]}]}, {"delay_min": 15, "targets": [{"type": "user", "id": _PEOPLE[2]["id"]}]}]}]
@app.get("/api/v1/oncall/escalations")
def oc_runs(): return [{"id": "5e000000-0000-4000-8000-0000000000c1", "status": "active", "step": 1, "cycle": 0, "next_at": iso(now + timedelta(minutes=11)),
    "job_name": "customer-report-generator", "condition": "failed", "policy": "Platform", "incident_id": None, "job_id": JOBS[3]["id"],
    "history": [{"at": iso(now - timedelta(minutes=4)), "step": 1, "cycle": 1, "paged": ["Ana Rao"], "notes": []}], "created_at": iso(now - timedelta(minutes=4))}]
@app.get("/api/v1/alerts/maintenance")   # R63
def maintenance(): return [
    {"id": "6f1c2d3e-0000-4000-8000-0000000000d1", "name": "Sunday database patching", "scope": {"tags": ["db"]}, "starts_at": "2026-09-06T20:30:00Z",
     "ends_at": "2026-09-06T22:30:00Z", "tz": "Asia/Kolkata", "rrule": "FREQ=WEEKLY;BYDAY=SU", "active_now": False, "current_end": None,
     "next_start": "2026-10-11T20:30:00Z", "next_end": "2026-10-11T22:30:00Z"},
    {"id": "6f1c2d3e-0000-4000-8000-0000000000d2", "name": "Cluster upgrade", "scope": {}, "starts_at": "2026-10-05T11:00:00Z", "ends_at": "2026-10-05T15:00:00Z",
     "tz": "UTC", "rrule": None, "active_now": True, "current_end": "2026-10-05T15:00:00Z", "next_start": None, "next_end": None}]
@app.get("/api/v1/metric-exports")   # R58
def metric_exports(): return [{"id": "6f1c2d3e-0000-4000-8000-0000000000c1", "kind": "datadog", "name": "Datadog", "site": "datadoghq.eu", "enabled": True,
                               "points_total": 482113, "last_success_at": "2026-10-05T12:01:00Z", "last_error": None, "last_error_at": None, "next_attempt_at": "2026-10-05T12:02:00Z"}]
@app.get("/api/v1/audit-streams")   # R57
def audit_streams(): return [{"id": "6f1c2d3e-0000-4000-8000-0000000000b1", "kind": "splunk_hec", "name": "Security SIEM", "host": "splunk.acme.com", "enabled": True,
                              "delivered_total": 18342, "pending": 0, "last_success_at": "2026-10-05T11:58:02Z", "last_error": None, "last_error_at": None,
                              "next_attempt_at": "2026-10-05T11:58:02Z", "created_at": "2026-09-01T09:00:00Z"},
                             {"id": "6f1c2d3e-0000-4000-8000-0000000000b2", "kind": "webhook", "name": "Log pipeline", "host": "logs.acme.com", "enabled": True,
                              "delivered_total": 9211, "pending": 37, "last_success_at": "2026-10-05T11:41:10Z", "last_error": "destination answered HTTP 503",
                              "last_error_at": "2026-10-05T11:57:40Z", "next_attempt_at": "2026-10-05T12:02:40Z", "created_at": "2026-09-14T09:00:00Z"}]
@app.get("/api/v1/sso/scim-groups")   # R56
def scim_groups(): return [{"id": "6f1c2d3e-0000-4000-8000-0000000000a1", "display_name": "JobWatch Admins", "role": "admin", "members": 2, "updated_at": "2026-10-05T09:00:00Z"},
                           {"id": "6f1c2d3e-0000-4000-8000-0000000000a2", "display_name": "SRE", "role": "sre", "members": 7, "updated_at": "2026-10-05T09:00:00Z"},
                           {"id": "6f1c2d3e-0000-4000-8000-0000000000a3", "display_name": "All Engineering", "role": None, "members": 42, "updated_at": "2026-10-05T09:00:00Z"}]
@app.get("/api/v1/sso/scim-tokens")   # R44
def scim_tokens(): return {"base_url": "https://api.jobwatch.example/scim/v2", "tokens": [{"id": "6f1c2d3e-0000-4000-8000-000000000001", "name": "Okta", "prefix": "jwscim_1a2b3c4d", "created_at": "2026-10-01T09:10:00Z", "last_used_at": "2026-10-01T11:42:00Z"}]}
@app.get("/api/v1/sso")   # R43
def sso(): return {"configured": True, "protocol": "oidc", "display_name": "Okta", "enabled": True, "enforce": False, "default_role": "developer",
                   "config": {"issuer": "https://acme.okta.com", "client_id": "0oa1b2c3"}, "has_client_secret": True, "updated_at": "2026-10-01T09:00:00Z",
                   "redirect_uri": "https://sso.jobwatch.example/realms/cronsentinel/broker/org-o/endpoint",
                   "domains": [{"id": "d1", "domain": "acme.com", "verified_at": "2026-10-01T09:05:00Z", "txt_name": "_jobwatch-verify.acme.com", "txt_value": "jobwatch-verify=abc"},
                               {"id": "d2", "domain": "acme.io", "verified_at": None, "txt_name": "_jobwatch-verify.acme.io", "txt_value": "jobwatch-verify=def"}]}
@app.post("/api/v1/jobs/schedule/preview")
def prev(expr: str, tz: str = "UTC"):
    from cronsentinel import schedule
    return {"expr": expr, "human": schedule.human(expr), "next": [iso(x) for x in schedule.next_runs(expr, tz, 5)]}
