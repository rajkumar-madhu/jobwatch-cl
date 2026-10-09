"""R50: Cronitor import + Cronitor-compatible telemetry paths, against tests/fakes/cronitor.py
(a stand-in written to Cronitor's public API docs — Cronitor itself is closed source)."""
import socket
import threading
import time

import pytest
import uvicorn
from sqlalchemy import text

from cronsentinel.importers import cronitor as cr
from tests.fakes import cronitor as fake

TK = "tk_live_abc123"


@pytest.fixture(scope="module")
def fake_cronitor():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(fake.app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def clients(org, monkeypatch):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.config import settings
    from cronsentinel.db import system_session
    from cronsentinel.ingest_main import app as ingest
    from cronsentinel.main import app
    monkeypatch.setattr(settings, "outbound_allow_private", True)   # the fake runs on localhost
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='business' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'a',:p,:h,'admin')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    ingest.state.js = None
    return TestClient(app, raise_server_exceptions=False), TestClient(ingest, raise_server_exceptions=False), {"X-API-Key": raw}


def test_schedule_translation():
    assert cr.translate_schedule("0 2 * * *") == ("0 2 * * *", 0, None)
    assert cr.translate_schedule("every 5 minutes")[0] == "*/5 * * * *"
    assert cr.translate_schedule("every hour")[0] == "0 * * * *"
    assert cr.translate_schedule("at 09:30")[0] == "30 9 * * *"
    assert cr.translate_schedule("every 90 minutes")[0] is None
    assert cr.translate_schedule("whenever")[0] is None


def test_mapping_rules():
    m = cr.map_monitor(fake.MONITORS[0], TK)
    j = m["job"]
    assert m["status"] == "imported" and j["schedule_expr"] == "0 2 * * *" and j["tz"] == "Asia/Kolkata"
    assert j["grace_s"] == 1800 and j["expected_runtime_s"] == 7200 and j["tags"] == ["prod", "db"]
    assert m["token"] == f"{TK}/nightly-backup" and j["assertions"] == ["metric.count > 0"]     # R54: enforced now
    assert cr.map_monitor({"key": "x", "type": "job"}, None)["job"]["grace_s"] == 60          # Cronitor job default
    assert cr.map_monitor({"key": "x", "type": "heartbeat"}, None)["job"]["grace_s"] == 0
    assert cr.map_monitor(fake.MONITORS[4], TK)["status"] == "skipped"                       # type check
    two = cr.map_monitor({"key": "t", "type": "job", "schedules": ["0 6 * * *", "0 18 * * *"]}, TK)
    assert two["job"]["schedule_expr"] == "0 6 * * *" and "2 schedules" in two["note"]
    assert cr.map_monitor({"key": "bad key!", "type": "job"}, TK)["token"] is None


@pytest.mark.parametrize("envelope", ["monitors", "data", "list"])
def test_fetch_pages_through_every_envelope_shape(fake_cronitor, envelope, monkeypatch):
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    fake.ENVELOPE["value"] = envelope
    fake.calls.clear()
    try:
        mons = cr.fetch(fake.READ_KEY, fake_cronitor)
    finally:
        fake.ENVELOPE["value"] = "monitors"
    assert len(mons) == len(fake.MONITORS) == 101
    assert [c["page"] for c in fake.calls] == [1, 2] and all(c["version"] == cr.CRONITOR_VERSION for c in fake.calls)
    assert any(m.get("schedules") == ["0 6 * * *", "0 18 * * *"] for m in mons)          # pinned version shows all schedules


def test_bad_keys(fake_cronitor, monkeypatch):
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    with pytest.raises(cr.ImportError_, match="monitor:read"):
        cr.fetch(fake.NO_SCOPE_KEY, fake_cronitor)
    with pytest.raises(cr.ImportError_, match="rejected"):
        cr.fetch("wrong_key_000000", fake_cronitor)


def _execs(org_id, name):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("""SELECT e.status::text, e.exit_code, e.duration_ms FROM executions e JOIN jobs j ON j.id=e.job_id
            WHERE j.org_id=:o AND j.name=:n ORDER BY e.server_received_ts"""), {"o": org_id, "n": name}).all()


def test_import_then_ping_with_cronitor_paths(fake_cronitor, clients, org):
    api, ingest, h = clients
    req = {"api_key": fake.READ_KEY, "telemetry_key": TK, "base_url": fake_cronitor, "workspace_id": str(org["ws"])}
    # a script already pointed at JobWatch before the import: 404 (and cached as unknown)
    assert ingest.get(f"/cronitor/p/{TK}/nightly-backup").status_code == 404
    dry = api.post("/api/v1/import/cronitor", headers=h, json={**req, "dry_run": True}).json()
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM jobs WHERE org_id=:o"), {"o": org["id"]}).scalar() == 0
    assert len(dry["results"]) == 101
    r = api.post("/api/v1/import/cronitor", headers=h, json={**req, "dry_run": False}).json()
    by = {x["name"]: x for x in r["results"]}
    assert by["Nightly backup"]["status"] == "imported" and by["Nightly backup"]["kept_ping_url"]
    assert by["Queue worker"]["schedule_expr"] == "*/5 * * * *" and by["Queue worker"]["status"] == "converted"
    assert by["Daily report"]["schedule_expr"] == "30 9 * * *"
    assert by["Odd job"]["status"] == "needs_schedule" and by["API uptime"]["status"] == "skipped"
    assert "/cronitor/" in r["ping_host_change"]
    with system_session() as s:
        rt = s.execute(text("SELECT expected_runtime_s FROM jobs WHERE org_id=:o AND name='Nightly backup'"), {"o": org["id"]}).scalar()
    assert rt == 7200
    again = api.post("/api/v1/import/cronitor", headers=h, json={**req, "dry_run": False}).json()
    assert not any(x["job_id"] for x in again["results"])
    # ping exactly as a Cronitor script would, only the host changed
    base = f"/cronitor/p/{TK}/nightly-backup"
    assert ingest.get(base, params={"state": "run", "series": "s-1"}).text == "OK"
    assert ingest.get(base, params=[("state", "complete"), ("series", "s-1"), ("metric", "duration:12.5"), ("metric", "count:3")]).status_code == 200
    assert ingest.post(f"/cronitor/ping/{TK}/nightly-backup", params={"state": "fail", "message": "pg_dump: refused", "status_code": "2"},
                       content=b"ignored").status_code == 200
    rows = _execs(org["id"], "Nightly backup")
    assert [x.status for x in rows] == ["success", "failed"], rows      # run + complete paired by series
    # R54: the imported "metric.count > 0" is enforced — a complete with count 0 is a failure
    ingest.get(base, params=[("state", "complete"), ("metric", "count:0")])
    from cronsentinel.db import system_session
    with system_session() as s:
        last = s.execute(text("""SELECT e.status::text, e.metrics, e.meta->'assertion_failures' AS af FROM executions e JOIN jobs j ON j.id=e.job_id
            WHERE j.org_id=:o AND j.name='Nightly backup' ORDER BY e.server_received_ts DESC LIMIT 1"""), {"o": org["id"]}).first()
    assert last.status == "failed" and last.metrics == {"count": 0.0} and last.af == ["metric.count > 0 (was 0)"]
    assert rows[0].duration_ms == 12500 and rows[1].exit_code == 2
    assert ingest.get(f"/cronitor/p/{TK}/no-such-monitor").status_code == 404
    assert ingest.get("/cronitor/p/wrong-key/nightly-backup").status_code == 404     # the key is part of the credential
    assert ingest.get(base, params={"state": "explode"}).status_code == 400


def test_without_telemetry_key_jobs_get_new_urls(fake_cronitor, clients, org):
    api, ingest, h = clients
    r = api.post("/api/v1/import/cronitor", headers=h, json={"api_key": fake.READ_KEY, "base_url": fake_cronitor,
                                                            "workspace_id": str(org["ws"]), "dry_run": False}).json()
    made = [x for x in r["results"] if x["job_id"]]
    assert made and all(not x["kept_ping_url"] and x["new_ping_url"] for x in made)
    tok = made[0]["new_ping_url"].rsplit("/ping/", 1)[1]
    assert ingest.get(f"/ping/{tok}").status_code == 200


def test_private_base_url_refused_by_default(clients, org, monkeypatch):
    api, _, h = clients
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", False)
    r = api.post("/api/v1/import/cronitor", headers=h, json={"api_key": "x" * 10, "base_url": "http://127.0.0.1:9/", "workspace_id": str(org["ws"])})
    assert r.status_code == 400
