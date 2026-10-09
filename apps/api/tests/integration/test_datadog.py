"""R58: Datadog — alert events, audit logs and job metrics, against a recording mock of Datadog's API."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from cronsentinel import datadog
from tests.integration.test_alert_context import world  # noqa: F401  (fixture: job on prod-db-01 / production / team Data)

KEY = "0123456789abcdef0123456789abcdef"
calls: list[dict] = []
answer = {"status": 202}


@pytest.fixture(autouse=True)
def fake_datadog(monkeypatch):
    calls.clear(); answer["status"] = 202
    real = httpx.Client

    def handler(req: httpx.Request):
        calls.append({"host": req.url.host, "path": req.url.path, "key": req.headers.get("dd-api-key"),
                      "body": json.loads(req.content) if req.content else None})
        return httpx.Response(answer["status"], json={"valid": answer["status"] == 200} if req.url.path.endswith("validate") else {})
    monkeypatch.setattr(datadog, "_client", lambda timeout: real(transport=httpx.MockTransport(handler)))


@pytest.fixture
def owner(org):
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='business' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("DELETE FROM metric_exports")); s.execute(text("DELETE FROM audit_streams"))
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'own',:p,:h,'owner')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    from fastapi.testclient import TestClient

    from cronsentinel.main import app
    return TestClient(app, raise_server_exceptions=False), {"X-API-Key": raw}


def test_validate_and_sites():
    assert datadog.validate({"api_key": KEY, "site": "datadoghq.eu"}) is None
    assert "32-character" in datadog.validate({"api_key": "short"})
    assert "site must be" in datadog.validate({"api_key": KEY, "site": "evil.example.com"})


def test_alert_event_carries_context_through_the_real_notifier(world, owner):  # noqa: F811
    c, h = owner
    assert c.post("/api/v1/alerts/channels", headers=h, json={"kind": "datadog", "name": "dd", "config": {"api_key": "nope"}}).status_code == 400
    r = c.post("/api/v1/alerts/channels", headers=h, json={"kind": "datadog", "name": "dd", "config": {"api_key": KEY, "site": "us5.datadoghq.com"}})
    assert r.status_code == 201, r.text
    from cronsentinel.alerting import tasks
    payload = {"job_id": world["job"], "job_name": "x", "prev_status": "healthy", "new_status": "failed", "condition": "failed",
               "severity": "high", "incident_id": None, "execution_id": world["exec"], "ts": "t"}
    from cronsentinel.db import system_session
    with system_session() as s:
        ch = s.execute(text("SELECT id FROM notification_channels WHERE org_id=:o AND kind='datadog'"), {"o": world["org"]}).scalar()
    tasks.send_notification.run(world["org"], str(ch), f"dk-{uuid.uuid4().hex}", None, payload)
    ev = calls[-1]
    assert (ev["host"], ev["path"], ev["key"]) == ("api.us5.datadoghq.com", "/api/v1/events", KEY)
    b = ev["body"]
    assert b["alert_type"] == "error" and b["host"] == "prod-db-01" and b["aggregation_key"] == f"jobwatch-job-{world['job']}"
    assert {"source:jobwatch", "host:prod-db-01", "env:production", "team:data", "severity:high"} <= set(b["tags"])
    assert "on prod-db-01 (production)" in b["title"] and "Failed checks" in b["text"]


def test_audit_log_to_datadog_logs(owner, org):
    c, h = owner
    r = c.post("/api/v1/audit-streams", headers=h, json={"kind": "datadog_logs", "name": "DD", "api_key": KEY, "site": "datadoghq.eu"})
    assert r.status_code == 201 and KEY not in r.text and r.json()["host"] == "http-intake.logs.datadoghq.eu"
    from cronsentinel import auditstream
    from cronsentinel.db import system_session
    with system_session() as s:
        auditstream.tick(s)
    req = calls[-1]
    assert (req["host"], req["path"], req["key"]) == ("http-intake.logs.datadoghq.eu", "/api/v2/logs", KEY)
    item = req["body"][0]
    assert item["ddsource"] == "jobwatch" and item["jobwatch"]["action"] == "audit_stream.create" and "action:audit_stream.create" in item["ddtags"]


def test_job_metrics_export(world, owner):  # noqa: F811
    c, h = owner
    from cronsentinel import metricsexport
    from cronsentinel.db import system_session
    r = c.post("/api/v1/metric-exports", headers=h, json={"api_key": KEY, "site": "datadoghq.com", "tags": "team_owner:platform"})
    assert r.status_code == 201 and KEY not in r.text
    now = datetime.now(UTC)
    with system_session() as s:
        s.execute(text("UPDATE metric_exports SET cursor=:c"), {"c": now - timedelta(minutes=10)})
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, agent_ts_start, agent_ts_end, duration_ms, exit_code)
                          VALUES (:id, :o, :j, 'success', :t, :t, :t, 1500, 0)"""), {"id": f"ok-{uuid.uuid4().hex[:6]}", "o": world["org"], "j": world["job"], "t": now - timedelta(minutes=5)})
        s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline, state, settled_at)
                          VALUES (:o, :j, :t, :t, :t, 'missed', :t)"""), {"o": world["org"], "j": world["job"], "t": now - timedelta(minutes=4)})
        n = metricsexport.tick(s, now)
    assert n > 0
    req = calls[-1]
    assert (req["host"], req["path"], req["key"]) == ("api.datadoghq.com", "/api/v2/series", KEY)
    series = req["body"]["series"]
    names = [x["metric"] for x in series]
    assert {"jobwatch.run.completed", "jobwatch.run.duration", "jobwatch.run.missed", "jobwatch.job.failing"} <= set(names)
    dur = next(x for x in series if x["metric"] == "jobwatch.run.duration")
    assert dur["points"][0]["value"] == 1.5 and {"status:success", "env:production", "team_owner:platform"} <= set(dur["tags"])
    failing = next(x for x in series if x["metric"] == "jobwatch.job.failing" and "host:prod-db-01" in x["tags"])
    assert failing["points"][0]["value"] == 1                       # the world job's status is 'failed'
    # the window advanced: an immediate second tick sends nothing new
    calls.clear()
    with system_session() as s:
        assert metricsexport.tick(s, now + timedelta(seconds=5)) == 0


def test_metrics_outage_keeps_the_window(world, owner):  # noqa: F811
    c, h = owner
    from cronsentinel import metricsexport
    from cronsentinel.db import system_session
    c.post("/api/v1/metric-exports", headers=h, json={"api_key": KEY})
    now = datetime.now(UTC)
    with system_session() as s:
        s.execute(text("UPDATE metric_exports SET cursor=:c"), {"c": now - timedelta(minutes=10)})
    answer["status"] = 403
    with system_session() as s:
        assert metricsexport.tick(s, now) == 0
        cur = s.execute(text("SELECT cursor, last_error FROM metric_exports")).first()
    assert cur.last_error == "Datadog rejected the API key" and cur.cursor < now - timedelta(minutes=9)
    st = c.get("/api/v1/metric-exports", headers=h).json()[0]
    assert st["last_error"] == "Datadog rejected the API key"
    assert c.post(f"/api/v1/metric-exports/{st['id']}/test", headers=h).json() == {"ok": False, "error": "Datadog rejected the API key"}
    answer["status"] = 200
    assert c.post(f"/api/v1/metric-exports/{st['id']}/test", headers=h).json() == {"ok": True}


def test_metrics_export_access_and_plan(owner, org):
    c, h = owner
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'dev',:p,:h,'devops')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    assert c.post("/api/v1/metric-exports", headers={"X-API-Key": raw}, json={"api_key": KEY}).status_code == 403
    assert c.post("/api/v1/metric-exports", headers=h, json={"api_key": KEY, "site": "nope.example"}).status_code == 422
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='developer' WHERE id=:o"), {"o": org["id"]})
    assert c.post("/api/v1/metric-exports", headers=h, json={"api_key": KEY}).status_code == 402
