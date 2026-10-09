"""R48: Healthchecks import + Healthchecks-compatible ping paths.

The live test runs against a real Healthchecks server (open source) when HC_LIVE_URL and
HC_LIVE_KEY are set; the mapping test always runs."""
import os
import uuid

import pytest
from sqlalchemy import text

from cronsentinel.importers import healthchecks as hc

U = "0357c0b7-52c7-4705-b2f3-e0e11b205268"


def test_mapping_rules():
    cron = hc.map_check({"name": "b", "schedule": "0 2 * * *", "tz": "Asia/Kolkata", "grace": 3600, "tags": "prod db", "desc": "d", "uuid": U})
    assert cron["status"] == "imported" and cron["job"]["schedule_expr"] == "0 2 * * *" and cron["job"]["tz"] == "Asia/Kolkata"
    assert cron["job"]["tags"] == ["prod", "db"] and cron["token"] == U
    hourly = hc.map_check({"name": "h", "timeout": 3600, "grace": 600})
    assert hourly["status"] == "converted" and hourly["job"]["schedule_expr"] == "0 * * * *" and hourly["job"]["grace_s"] == 4200
    odd = hc.map_check({"name": "o", "timeout": 5400, "grace": 300})
    assert odd["status"] == "needs_schedule" and odd["job"]["schedule_expr"] is None
    cal = hc.map_check({"name": "w", "schedule": "Mon..Fri 09:00", "tz": "Europe/London"})
    assert cal["status"] == "converted" and cal["job"]["schedule_expr"] == "0 9 * * 1,2,3,4,5" and cal["job"]["tz"] == "Europe/London"
    odd = hc.map_check({"name": "y", "schedule": "*-*-* 03:00:30", "tz": "UTC"})
    assert odd["status"] == "skipped" and "no exact cron form" in odd["note"]
    assert hc.map_check({"name": "p", "timeout": 60, "grace": 60, "status": "paused"})["job"]["paused"] is True
    assert hc.map_check({"name": "r", "timeout": 60, "unique_key": "abc"})["token"] is None   # read-only key: no uuid


@pytest.fixture
def clients(org):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.ingest_main import app as ingest
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='business' WHERE id=:o"), {"o": org["id"]})
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'a',:p,:h,'admin')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    ingest.state.js = None
    return TestClient(app, raise_server_exceptions=False), TestClient(ingest, raise_server_exceptions=False), {"X-API-Key": raw}


@pytest.mark.skipif(not os.getenv("HC_LIVE_URL"), reason="needs a running Healthchecks server (HC_LIVE_URL, HC_LIVE_KEY)")
def test_live_import_then_ping_with_unchanged_paths(clients, org, monkeypatch):
    api, ingest, h = clients
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", True)   # the test server is on localhost
    req = {"api_key": os.environ["HC_LIVE_KEY"], "base_url": os.environ["HC_LIVE_URL"], "workspace_id": str(org["ws"])}
    # a script already pointed at JobWatch pings before the import: 404, and cached as unknown for 60 s
    assert ingest.get(f"/hc/{os.environ['HC_LIVE_BACKUP_UUID']}").status_code == 404
    dry = api.post("/api/v1/import/healthchecks", headers=h, json={**req, "dry_run": True})
    assert dry.status_code == 200, dry.text
    from cronsentinel.db import system_session
    with system_session() as s:
        assert s.execute(text("SELECT count(*) FROM jobs WHERE org_id=:o"), {"o": org["id"]}).scalar() == 0   # dry run writes nothing
    r = api.post("/api/v1/import/healthchecks", headers=h, json={**req, "dry_run": False}).json()
    by = {x["name"]: x for x in r["results"]}
    assert by["nightly-db-backup"]["status"] == "imported" and by["nightly-db-backup"]["kept_ping_url"]
    assert by["hourly-sync"]["schedule_expr"] == "0 * * * *" and by["odd-90min"]["status"] == "needs_schedule"
    assert by["weekday-report"]["status"] == "converted" and by["weekday-report"]["schedule_expr"] == "0 9 * * 1,2,3,4,5"   # R53
    assert "/hc/" in r["ping_host_change"]
    # re-running does not duplicate
    again = api.post("/api/v1/import/healthchecks", headers=h, json={**req, "dry_run": False}).json()
    assert all(x["status"] in ("exists", "skipped") for x in again["results"])
    # ping exactly the way a Healthchecks script would, only the host changed
    uuid_ = os.environ["HC_LIVE_BACKUP_UUID"]
    rid = str(uuid.uuid4())
    assert ingest.get(f"/hc/{uuid_}/start", params={"rid": rid}).text == "OK"
    assert ingest.post(f"/hc/{uuid_}/0", params={"rid": rid}, content=b"backup done 12 GB").status_code == 200
    assert ingest.get(f"/hc/{uuid_}/log").text == "OK"
    assert ingest.get(f"/hc/{uuid_}/fail").status_code == 200
    with system_session() as s:
        rows = s.execute(text("""SELECT e.status::text, e.exit_code FROM executions e JOIN jobs j ON j.id=e.job_id
            WHERE j.org_id=:o AND j.name='nightly-db-backup' ORDER BY e.server_received_ts"""), {"o": org["id"]}).all()
    assert [r.status for r in rows] == ["success", "failed"], rows     # start+0 paired by rid into one run
    assert rows[0].exit_code == 0
    assert ingest.get("/hc/00000000-0000-0000-0000-000000000000").status_code == 404


def test_unknown_action_and_bad_key(clients, org):
    api, ingest, h = clients
    assert ingest.get(f"/hc/{U}/explode").status_code == 400
    r = api.post("/api/v1/import/healthchecks", headers=h, json={"api_key": "x" * 10, "base_url": "http://127.0.0.1:9/", "workspace_id": str(org["ws"])})
    assert r.status_code == 400   # private address refused by the SSRF guard (no OUTBOUND_ALLOW_PRIVATE)


@pytest.mark.skipif(not os.getenv("HC_LIVE_RO_KEY"), reason="needs HC_LIVE_URL and HC_LIVE_RO_KEY")
def test_live_read_only_key_issues_new_ping_urls(clients, org, monkeypatch):
    api, ingest, h = clients
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    r = api.post("/api/v1/import/healthchecks", headers=h, json={"api_key": os.environ["HC_LIVE_RO_KEY"], "base_url": os.environ["HC_LIVE_URL"],
                                                                "workspace_id": str(org["ws"]), "dry_run": False}).json()
    created = [x for x in r["results"] if x["job_id"]]
    assert created and all(not x["kept_ping_url"] and x["new_ping_url"] for x in created)
    tok = created[0]["new_ping_url"].rsplit("/ping/", 1)[1]
    assert ingest.get(f"/ping/{tok}").status_code == 200 and ingest.get(f"/hc/{tok}").text == "OK"
