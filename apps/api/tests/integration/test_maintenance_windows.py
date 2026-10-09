"""R63: maintenance windows end to end — reconciler, alert engine, API, status page."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


@pytest.fixture
def api(org):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'d',:p,:h,'devops')"), {"o": org["id"], "p": prefix, "h": hash_secret(raw)})
    return TestClient(app, raise_server_exceptions=False), {"X-API-Key": raw}


def _window(org_id, start, end, rrule=None, scope=None, name="Patch night", tz="UTC"):
    from cronsentinel.db import system_session
    with system_session() as s:
        return str(s.execute(text("""INSERT INTO maintenance_windows (org_id, name, scope, starts_at, ends_at, rrule, tz)
            VALUES (:o, :n, CAST(:sc AS jsonb), :s, :e, :r, :tz) RETURNING id"""),
            {"o": org_id, "n": name, "sc": json.dumps(scope or {}), "s": start, "e": end, "r": rrule, "tz": tz}).scalar())


def _slot(org_id, job_id, at, state="pending"):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline, state)
            VALUES (:o, :j, :t, :g, :d, :st) RETURNING id"""),
            {"o": org_id, "j": job_id, "t": at, "g": at + timedelta(minutes=1), "d": at + timedelta(minutes=2), "st": state}).scalar()


def _state(slot_id):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT state::text FROM expected_runs WHERE id=:i"), {"i": slot_id}).scalar()


def test_weekly_window_still_skips_runs_weeks_after_it_was_created(org, make_job):
    """The R63 bug: the reconciler only saw a window's first occurrence, so from the second week
    runs inside a recurring window were marked missed and alerted."""
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    first = now - timedelta(weeks=4, minutes=20)                 # same weekday and time, four weeks ago
    day = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][first.weekday()]
    _window(org["id"], first, first + timedelta(hours=2), rrule=f"FREQ=WEEKLY;BYDAY={day}")
    j = make_job(schedule="*/5 * * * *", created_ago=timedelta(days=60))
    inside = _slot(org["id"], j, now - timedelta(minutes=10))     # inside this week's occurrence
    with system_session() as s:
        reconciler.tick(s)
    assert _state(inside) == "skipped"


def test_scoped_window_only_skips_its_jobs(org, make_job):
    from cronsentinel.db import system_session
    from cronsentinel.workers import reconciler
    now = datetime.now(UTC)
    a, b = make_job(name="db-backup", created_ago=timedelta(days=2)), make_job(name="web-sync", created_ago=timedelta(days=2))
    with system_session() as s:
        s.execute(text("UPDATE jobs SET tags=ARRAY['db'] WHERE id=:j"), {"j": a})
    _window(org["id"], now - timedelta(hours=1), now + timedelta(hours=1), scope={"tags": ["db"]})
    sa, sb = _slot(org["id"], a, now - timedelta(minutes=10)), _slot(org["id"], b, now - timedelta(minutes=10))
    with system_session() as s:
        reconciler.tick(s)
    assert _state(sa) == "skipped" and _state(sb) in ("late", "missed")


def test_alert_engine_suppresses_inside_a_recurring_window(org):
    from cronsentinel.alerting import engine
    from cronsentinel.alerting import rules as R
    from cronsentinel.db import system_session
    now = datetime.now(UTC)
    first = now - timedelta(days=21, minutes=5)
    day = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][first.weekday()]
    _window(org["id"], first, first + timedelta(minutes=30), rrule=f"FREQ=WEEKLY;BYDAY={day}")
    job = R.JobCtx(id=str(uuid.uuid4()), name="x", tags=[], team_id=None, environment_id=None, workspace_id=None, consecutive_failures=0)
    with system_session() as s:
        assert engine._in_maintenance(s, org["id"], job, now)
        assert not engine._in_maintenance(s, org["id"], job, now + timedelta(hours=2))


def test_api_create_list_end_delete(api, org):
    c, h = api
    now = datetime.now(UTC).replace(microsecond=0)
    bad = c.post("/api/v1/alerts/maintenance", headers=h, json={"name": "x", "starts_at": now.isoformat(), "ends_at": now.isoformat()})
    assert bad.status_code == 422 and "end after" in bad.text
    assert c.post("/api/v1/alerts/maintenance", headers=h, json={"starts_at": now.isoformat(), "ends_at": (now + timedelta(hours=1)).isoformat(),
                  "repeat": "weekly"}).status_code == 422
    first = now - timedelta(days=30)
    r = c.post("/api/v1/alerts/maintenance", headers=h, json={"name": "Sunday patching", "starts_at": first.isoformat(),
               "ends_at": (first + timedelta(hours=2)).isoformat(), "tz": "Asia/Kolkata", "repeat": "weekly", "weekdays": ["SU"],
               "scope": {"tags": ["db"]}})
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["rrule"] == "FREQ=WEEKLY;BYDAY=SU" and w["next_start"] and w["scope"] == {"tags": ["db"]}
    listed = c.get("/api/v1/alerts/maintenance", headers=h).json()
    assert any(x["id"] == w["id"] for x in listed)            # a 30-day-old recurring window is still listed
    assert c.post(f"/api/v1/alerts/maintenance/{w['id']}/end", headers=h).status_code == 409
    one = c.post("/api/v1/alerts/maintenance", headers=h, json={"name": "DB upgrade", "starts_at": (now - timedelta(minutes=5)).isoformat(),
                 "ends_at": (now + timedelta(hours=2)).isoformat()}).json()
    assert one["active_now"] is True
    ended = c.post(f"/api/v1/alerts/maintenance/{one['id']}/end", headers=h)
    assert ended.status_code == 200 and ended.json()["active_now"] is False
    assert c.delete(f"/api/v1/alerts/maintenance/{w['id']}", headers=h).status_code == 204
    from cronsentinel.db import system_session
    with system_session() as s:
        acts = [r.action for r in s.execute(text("SELECT action FROM audit_logs WHERE org_id=:o AND action LIKE 'maintenance.%'"), {"o": org["id"]}).all()]
    assert {"maintenance.create", "maintenance.end", "maintenance.delete"} <= set(acts)


def test_status_page_shows_only_windows_for_its_jobs(api, org, make_job):
    c, _ = api
    from cronsentinel.db import system_session
    now = datetime.now(UTC)
    public, internal = make_job(name="billing-run"), make_job(name="internal-etl")
    slug = f"st-{uuid.uuid4().hex[:6]}"
    with system_session() as s:
        s.execute(text("UPDATE jobs SET public_visibility='status' WHERE id=:j"), {"j": public})
        s.execute(text("INSERT INTO status_pages (org_id, slug, title, visibility, job_ids) VALUES (:o, :s, 'Acme', 'public', ARRAY[CAST(:j AS uuid)])"),
                  {"o": org["id"], "s": slug, "j": str(public)})
    _window(org["id"], now + timedelta(days=1), now + timedelta(days=1, hours=2), scope={"job_ids": [str(internal)]}, name="Internal ETL rebuild")
    first = now - timedelta(days=14) + timedelta(hours=3)
    _window(org["id"], first, first + timedelta(hours=1), rrule="FREQ=DAILY", scope={"job_ids": [str(public)]}, name="Billing maintenance")
    body = c.get(f"/public/status/{slug}").json()
    names = {m["name"] for m in body["maintenance"]}
    assert names == {"Billing maintenance"}                       # internal window no longer leaks onto public pages
    assert len(body["maintenance"]) == 5                          # daily window expanded to real upcoming dates



def test_creating_a_window_that_already_ended_works(api):
    """Found by the router smoke test: a past one-off window was saved, then not found → 500."""
    c, h = api
    r = c.post("/api/v1/alerts/maintenance", headers=h, json={"name": "Backfill record", "starts_at": "2026-01-01T00:00:00Z",
               "ends_at": "2026-01-01T01:00:00Z"})
    assert r.status_code == 201, r.text
    assert r.json()["active_now"] is False and r.json()["next_start"] is None


def test_get_window_by_id_even_long_after_it_ended(api):
    """R64 (Terraform reads): the list hides long-finished windows; reading one by id must not."""
    c, h = api
    w = c.post("/api/v1/alerts/maintenance", headers=h, json={"name": "Old", "starts_at": "2026-01-01T00:00:00Z",
               "ends_at": "2026-01-01T01:00:00Z"}).json()
    r = c.get(f"/api/v1/alerts/maintenance/{w['id']}", headers=h)
    assert r.status_code == 200 and r.json()["name"] == "Old"
    assert c.get("/api/v1/alerts/maintenance/00000000-0000-0000-0000-000000000000", headers=h).status_code == 404
