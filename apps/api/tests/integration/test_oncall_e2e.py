"""R42: on-call channels end to end — creation rules, plan gating, and a failure that pages then
auto-resolves on recovery with no "recovered" rule configured."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import text

PD_KEY = "R0123456789abcdefABCDEF012345678"


@pytest.fixture
def client_and_keys(org):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'k',:p,:h,'admin')"),
                  {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return TestClient(app, raise_server_exceptions=False), {"X-API-Key": raw}


def _plan(org, plan):
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan=:p WHERE id=:o"), {"p": plan, "o": org["id"]})


def test_channel_validation_and_plan_gating(client_and_keys, org):
    c, h = client_and_keys
    _plan(org, "team")
    bad = c.post("/api/v1/alerts/channels", headers=h, json={"kind": "pagerduty", "name": "pd", "config": {"routing_key": "nope"}})
    assert bad.status_code == 400 and "routing_key" in bad.json()["detail"]
    assert c.post("/api/v1/alerts/channels", headers=h, json={"kind": "opsgenie", "name": "og", "config": {"api_key": "x"}}).status_code == 400
    assert c.post("/api/v1/alerts/channels", headers=h, json={"kind": "pagerduty", "name": "pd", "config": {"routing_key": PD_KEY}}).status_code == 201
    _plan(org, "developer")
    assert c.post("/api/v1/alerts/channels", headers=h, json={"kind": "pagerduty", "name": "pd2", "config": {"routing_key": PD_KEY}}).status_code == 402
    _plan(org, "free")


def test_failure_pages_and_recovery_resolves_without_a_recovered_rule(org, make_job, monkeypatch):
    from cronsentinel.alerting import engine, oncall, tasks
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    sent = []
    real_client = httpx.Client
    def handler(req):
        sent.append(json.loads(req.content)); return httpx.Response(202, json={"status": "success"})
    monkeypatch.setattr(oncall.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    # Celery stand-in: queue during the engine's transaction, run after it commits (as a worker would)
    queued = []
    monkeypatch.setattr(engine.send_notification, "delay", lambda *a: queued.append(a))
    def flush():
        while queued:
            tasks.send_notification.run(*queued.pop(0))

    j = make_job()
    with system_session() as s:
        ch = s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc, rate_per_min) VALUES (:o,'pagerduty','pd',:c,30) RETURNING id"),
                       {"o": org["id"], "c": encrypt_json({"routing_key": PD_KEY})}).scalar()
        s.execute(text("INSERT INTO alert_rules (org_id, name, condition, severity, channel_ids, enabled) VALUES (:o,'fail','failed','critical',ARRAY[:ch]::uuid[],true)"),
                  {"o": org["id"], "ch": ch})
    base = {"org_id": org["id"], "job_id": str(j), "execution_id": f"e-{uuid.uuid4().hex}"}
    engine.handle_status_change({**base, "prev_status": "healthy", "new_status": "failed", "new_state": "failing"}); flush()
    assert [e["event_action"] for e in sent] == ["trigger"], sent
    engine.handle_status_change({**base, "prev_status": "failed", "new_status": "recovered", "new_state": "ok"}); flush()
    assert [e["event_action"] for e in sent] == ["trigger", "resolve"], sent
    assert sent[0]["dedup_key"] == sent[1]["dedup_key"] == f"jobwatch-job-{j}"
    # nothing left marked open
    assert not tasks._r.smembers(oncall.open_key(org["id"], str(j)))
