"""R57: audit-log streaming to a SIEM — delivery against a real local HTTP receiver."""
import hashlib
import hmac
import json
import socket
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn
from fastapi import FastAPI, Request
from sqlalchemy import text

from cronsentinel import auditstream

SECRET = "s" * 32
received: list[dict] = []
behaviour = {"status": 200, "delay": 0.0}
sink = FastAPI()


@sink.post("/{path:path}")
async def _recv(path: str, request: Request):
    body = await request.body()
    if behaviour["delay"]:
        time.sleep(behaviour["delay"])
    received.append({"path": path, "headers": dict(request.headers), "body": body})
    from fastapi.responses import JSONResponse
    return JSONResponse({"text": "Success", "code": 0}, status_code=behaviour["status"])


@pytest.fixture(scope="module")
def sink_url():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(sink, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def env(org, monkeypatch, sink_url):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.config import settings
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    monkeypatch.setattr(settings, "outbound_allow_private", True)        # the receiver is on localhost
    received.clear(); behaviour.update(status=200, delay=0.0)
    keys = {}
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='business' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("DELETE FROM audit_streams"))                       # tick() works across orgs: start clean
        for role in ("owner", "admin"):
            prefix, raw = generate_api_key()
            s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,:n,:p,:h,:r)"),
                      {"o": org["id"], "n": role, "p": prefix, "h": hash_secret(raw), "r": role})
            keys[role] = {"X-API-Key": raw}
    return TestClient(app, raise_server_exceptions=False), keys, sink_url


def _audit(org_id, action, n=1, ts=None):
    from cronsentinel.db import system_session
    with system_session() as s:
        for i in range(n):
            s.execute(text("INSERT INTO audit_logs (org_id, actor_type, action, target_type, target_id, payload, ts) VALUES (:o, 'user', :a, 'job', :t, CAST(:p AS jsonb), :ts)"),
                      {"o": org_id, "a": action, "t": f"j{i}", "p": json.dumps({"i": i}), "ts": ts or datetime.now(UTC)})


def _tick(now=None):
    from cronsentinel.db import system_session
    with system_session() as s:
        return auditstream.tick(s, now)


def _events():
    out = []
    for r in received:
        if r["path"].endswith("collector/event"):
            out += [json.loads(line)["event"] for line in r["body"].decode().split("\n")]
        else:
            out += json.loads(r["body"])["events"]
    return out


def test_webhook_delivers_in_order_signed_and_advances(env, org):
    c, k, url = env
    _audit(org["id"], "before.stream")                                  # not sent: stream starts "now"
    r = c.post("/api/v1/audit-streams", headers=k["owner"], json={"kind": "webhook", "name": "SIEM", "url": f"{url}/hook", "secret": SECRET})
    assert r.status_code == 201, r.text
    assert SECRET not in r.text and r.json()["host"] == "127.0.0.1"
    _audit(org["id"], "job.update", 3)
    assert _tick() >= 4                                                 # 3 + the stream's own creation event
    evs = _events()
    assert [e["action"] for e in evs] == ["audit_stream.create", "job.update", "job.update", "job.update"]
    assert [e["id"] for e in evs] == sorted(e["id"] for e in evs) and evs[1]["details"] == {"i": 0}
    req = received[0]
    sig = "sha256=" + hmac.new(SECRET.encode(), req["body"], hashlib.sha256).hexdigest()
    assert req["headers"]["x-jobwatch-signature"] == sig
    received.clear()
    assert _tick() == 0 and received == []                              # nothing new, nothing sent
    listed = c.get("/api/v1/audit-streams", headers=k["owner"]).json()[0]
    assert listed["pending"] == 0 and listed["delivered_total"] >= 4 and listed["last_error"] is None


def test_outage_keeps_events_and_retries_with_backoff(env, org):
    c, k, url = env
    c.post("/api/v1/audit-streams", headers=k["owner"], json={"kind": "webhook", "name": "S", "url": f"{url}/hook", "secret": SECRET})
    _audit(org["id"], "job.delete", 2)
    behaviour["status"] = 503
    now = datetime.now(UTC)
    assert _tick(now) == 0
    st = c.get("/api/v1/audit-streams", headers=k["owner"]).json()[0]
    assert st["last_error"] == "destination answered HTTP 503" and st["pending"] >= 3
    nxt = datetime.fromisoformat(st["next_attempt_at"])
    assert nxt > now and _tick(now + timedelta(seconds=1)) == 0          # backing off: not retried yet
    behaviour["status"] = 200
    received.clear()
    assert _tick(nxt + timedelta(seconds=1)) >= 3                        # recovered: everything arrives, in order
    assert [e["action"] for e in _events()][-2:] == ["job.delete", "job.delete"]
    assert c.get("/api/v1/audit-streams", headers=k["owner"]).json()[0]["last_error"] is None


def test_splunk_hec_format(env, org):
    c, k, url = env
    r = c.post("/api/v1/audit-streams", headers=k["owner"], json={"kind": "splunk_hec", "name": "Splunk", "url": f"{url}/services/collector/event",
                                                                   "token": "hec-token-123", "index": "security", "sourcetype": "jobwatch:audit"})
    assert r.status_code == 201 and "hec-token-123" not in r.text
    _audit(org["id"], "sso.configure")
    _tick()
    req = received[-1]
    assert req["headers"]["authorization"] == "Splunk hec-token-123"
    lines = [json.loads(x) for x in req["body"].decode().split("\n")]
    assert all(x["index"] == "security" and x["sourcetype"] == "jobwatch:audit" and isinstance(x["time"], float) for x in lines)
    assert lines[-1]["event"]["action"] == "sso.configure"


def test_only_this_orgs_events_and_backfill(env, org):
    c, k, url = env
    from cronsentinel.db import system_session
    other = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:i, 'other', :s)"), {"i": other, "s": f"o-{other.hex[:8]}"})
    _audit(org["id"], "old.event", ts=datetime.now(UTC) - timedelta(days=3))
    c.post("/api/v1/audit-streams", headers=k["owner"], json={"kind": "webhook", "name": "S", "url": f"{url}/hook", "secret": SECRET, "start": "30d"})
    _audit(str(other), "other.org.secret")
    _tick()
    acts = [e["action"] for e in _events()]
    assert "old.event" in acts and "other.org.secret" not in acts and {e["org_id"] for e in _events()} == {str(org["id"])}
    with system_session() as s:
        s.execute(text("DELETE FROM organizations WHERE id=:i"), {"i": other})


def test_two_exporters_do_not_double_send(env, org):
    c, k, url = env
    c.post("/api/v1/audit-streams", headers=k["owner"], json={"kind": "webhook", "name": "S", "url": f"{url}/hook", "secret": SECRET})
    _audit(org["id"], "x", 2)
    behaviour["delay"] = 1.0
    results = []
    threads = [threading.Thread(target=lambda: results.append(_tick())) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sorted(results)[0] == 0 and len(received) == 1               # the second exporter skipped the locked stream


def test_access_plan_https_and_test_endpoint(env, org, monkeypatch):
    c, k, url = env
    body = {"kind": "webhook", "name": "S", "url": f"{url}/hook", "secret": SECRET}
    assert c.post("/api/v1/audit-streams", headers=k["admin"], json=body).status_code == 403           # owners only
    assert c.post("/api/v1/audit-streams", headers=k["owner"], json={**body, "secret": None}).status_code == 422
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", False)
    assert c.post("/api/v1/audit-streams", headers=k["owner"], json=body).status_code == 400            # http + private address
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    sid = c.post("/api/v1/audit-streams", headers=k["owner"], json=body).json()["id"]
    assert c.post(f"/api/v1/audit-streams/{sid}/test", headers=k["owner"]).json() == {"ok": True}
    assert json.loads(received[-1]["body"])["events"][0]["action"] == "audit_stream.test"
    behaviour["status"] = 500
    assert c.post(f"/api/v1/audit-streams/{sid}/test", headers=k["owner"]).json() == {"ok": False, "error": "destination answered HTTP 500"}
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
    assert c.post("/api/v1/audit-streams", headers=k["owner"], json=body).status_code == 402
    assert c.delete(f"/api/v1/audit-streams/{sid}", headers=k["owner"]).status_code == 204
