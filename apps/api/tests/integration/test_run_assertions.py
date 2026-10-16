"""R54: a run that exits 0 but breaks a job assertion is a failure — via every ingest path."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.integration.test_agent_events import agent  # noqa: F401  (fixture)


@pytest.fixture
def env(org):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.ingest_main import app as ingest
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'d',:p,:h,'devops')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    ingest.state.js = None
    return TestClient(app, raise_server_exceptions=False), TestClient(ingest, raise_server_exceptions=False), {"X-API-Key": raw}


def _job(api, h, org, rules):
    r = api.post("/api/v1/jobs", headers=h, json={"workspace_id": str(org["ws"]), "name": f"bk-{uuid.uuid4().hex[:6]}", "assertions": rules})
    assert r.status_code == 201, r.text
    return r.json()


def _runs(api, h, jid):
    return api.get(f"/api/v1/jobs/{jid}/executions", headers=h).json()


def test_empty_backup_that_exits_zero_is_a_failure(env, org):
    api, ingest, h = env
    j = _job(api, h, org, ["metric.size_bytes > 0", "duration < 1h"])
    assert j["assertions"] == ["metric.size_bytes > 0", "duration < 1h"]
    t = j["heartbeat_token"]
    assert ingest.post(f"/api/v1/heartbeat/{t}", json={"status": "success", "exit_code": 0, "duration_ms": 40_000, "metrics": {"size_bytes": 0}}).status_code == 200
    assert ingest.post(f"/api/v1/heartbeat/{t}", json={"status": "success", "exit_code": 0, "duration_ms": 41_000, "metrics": {"size_bytes": 7_340_032}}).status_code == 200
    assert ingest.post(f"/api/v1/heartbeat/{t}", json={"status": "success", "exit_code": 0}).status_code == 200          # stopped reporting
    runs = _runs(api, h, j["id"])
    by_size = {r["metrics"].get("size_bytes"): r for r in runs}
    assert by_size[0.0]["status"] == "failed" and by_size[0.0]["assertion_failures"] == ["metric.size_bytes > 0 (was 0)"]
    assert by_size[7340032.0]["status"] == "success" and by_size[7340032.0]["assertion_failures"] == []
    silent = by_size[None]
    assert silent["status"] == "failed" and "metric not reported" in silent["assertion_failures"][0]
    assert any("duration unknown" in f for f in silent["assertion_failures"])


def test_start_then_success_uses_measured_duration(env, org):
    api, ingest, h = env
    j = _job(api, h, org, ["duration < 1s"])
    t, eid = j["heartbeat_token"], f"run-{uuid.uuid4().hex[:8]}"
    # Relative to now: the processor replaces an agent_ts more than 7 days old with server time.
    t0 = datetime.now(UTC) - timedelta(minutes=1)
    ingest.post(f"/api/v1/heartbeat/{t}", json={"status": "start", "execution_id": eid, "agent_ts": t0.isoformat()})
    ingest.post(f"/api/v1/heartbeat/{t}", json={"status": "success", "execution_id": eid, "sequence": 1, "agent_ts": (t0 + timedelta(seconds=5)).isoformat()})
    r = [x for x in _runs(api, h, j["id"]) if x["id"]][0]
    assert r["status"] == "failed" and r["assertion_failures"] == ["duration < 1s (took 5 s)"]


def test_failed_runs_are_not_re_evaluated_and_jobs_without_rules_are_unchanged(env, org):
    api, ingest, h = env
    j = _job(api, h, org, ["metric.rows > 0"])
    ingest.post(f"/api/v1/heartbeat/{j['heartbeat_token']}", json={"status": "fail", "exit_code": 3})
    r = _runs(api, h, j["id"])[0]
    assert r["status"] == "failed" and r["assertion_failures"] == []
    plain = _job(api, h, org, [])
    ingest.post(f"/api/v1/heartbeat/{plain['heartbeat_token']}", json={"status": "success", "metrics": {"rows": 0}})
    r = _runs(api, h, plain["id"])[0]
    assert r["status"] == "success" and r["metrics"] == {"rows": 0.0}


def test_job_state_goes_failing(env, org):
    api, ingest, h = env
    j = _job(api, h, org, ["metric.size_bytes > 0"])
    ingest.post(f"/api/v1/heartbeat/{j['heartbeat_token']}", json={"status": "success", "metrics": {"size_bytes": 0}})
    assert api.get(f"/api/v1/jobs/{j['id']}", headers=h).json()["status"] == "failed"


def test_agent_events_carry_metrics(env, org, agent):  # noqa: F811
    api, ingest, h = env
    j = _job(api, h, org, ["metric.rows >= 100"])
    from datetime import UTC, datetime
    now = datetime.now(UTC).isoformat()
    r = ingest.post("/agent/v1/events", headers={"X-Agent-Id": agent["id"], "X-Agent-Key": agent["key"]},
                    json={"events": [{"kind": "success", "job_token": j["heartbeat_token"], "execution_id": f"a-{uuid.uuid4().hex[:6]}",
                                      "agent_ts": now, "exit_code": 0, "duration_ms": 1000, "metrics": {"rows": 12, "bad key!": 5}}]})
    assert r.status_code == 200, r.text
    run = _runs(api, h, j["id"])[0]
    assert run["status"] == "failed" and run["metrics"] == {"rows": 12.0} and run["assertion_failures"] == ["metric.rows >= 100 (was 12)"]


def test_api_validation_and_clearing(env, org):
    api, _, h = env
    bad = api.post("/api/v1/jobs", headers=h, json={"workspace_id": str(org["ws"]), "name": "x", "assertions": ["metric.p95 < 5 over 24 hours"]})
    assert bad.status_code == 422 and "aggregate" in bad.text
    j = _job(api, h, org, ["metric.a > 1"])
    assert api.patch(f"/api/v1/jobs/{j['id']}", headers=h, json={"assertions": ["duration << 2"]}).status_code == 422
    r = api.patch(f"/api/v1/jobs/{j['id']}", headers=h, json={"assertions": ["metric.b  <  9"]})
    assert r.status_code == 200 and r.json()["assertions"] == ["metric.b < 9"]
    assert api.patch(f"/api/v1/jobs/{j['id']}", headers=h, json={"assertions": None}).json()["assertions"] == []
