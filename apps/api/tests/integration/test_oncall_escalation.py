"""R66: on-call schedules and escalation, through the real API, notifier and escalation engine."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

paged: list[dict] = []


@pytest.fixture
def world(org, monkeypatch):
    """Org on the Team plan with people Ana, Ben, Cy; keys for devops and developer; a capturing webhook channel."""
    from fastapi.testclient import TestClient

    from cronsentinel.alerting import tasks
    from cronsentinel.auth import generate_api_key, hash_secret
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    paged.clear()
    monkeypatch.setitem(tasks._SENDERS, "webhook", lambda cfg, title, body, p: paged.append({"title": title, "body": body}))
    people, keys = {}, {}
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='team' WHERE id=:o"), {"o": org["id"]})
        for n in ("Ana", "Ben", "Cy"):
            uid = s.execute(text("INSERT INTO users (keycloak_sub, email, name) VALUES (:k, :e, :n) RETURNING id"),
                            {"k": uuid.uuid4().hex, "e": f"{n.lower()}-{uuid.uuid4().hex}@acme.io", "n": n}).scalar()
            s.execute(text("INSERT INTO memberships (org_id, user_id, role) VALUES (:o, :u, 'sre')"), {"o": org["id"], "u": uid})
            people[n] = str(uid)
        for role in ("devops", "developer"):
            prefix, raw = generate_api_key()
            s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,:n,:p,:h,:r)"),
                      {"o": org["id"], "n": role, "p": prefix, "h": hash_secret(raw), "r": role})
            keys[role] = {"X-API-Key": raw}
        ch = s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc, rate_per_min) VALUES (:o,'webhook','pages',:c,60) RETURNING id"),
                       {"o": org["id"], "c": encrypt_json({"url": "https://hooks.example.test/p"})}).scalar()
    return {"c": TestClient(app, raise_server_exceptions=False), "p": people, "k": keys, "ch": str(ch), "org": org}


def _schedule(w, members, name=None, rotation="daily"):
    r = w["c"].post("/api/v1/oncall/schedules", headers=w["k"]["devops"], json={"name": name or f"Primary {uuid.uuid4().hex[:4]}", "tz": "UTC",
        "layers": [{"name": "main", "rotation": rotation, "handoff_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(), "members": members}]})
    assert r.status_code == 201, r.text
    return r.json()


def _policy(w, steps, repeat=0):
    r = w["c"].post("/api/v1/oncall/policies", headers=w["k"]["devops"], json={"name": f"Pol {uuid.uuid4().hex[:4]}", "repeat_count": repeat,
                    "delivery_channel_id": w["ch"], "steps": steps})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _esc_channel(w, pid):
    r = w["c"].post("/api/v1/alerts/channels", headers=w["k"]["devops"], json={"kind": "escalation", "name": f"esc-{pid[:4]}", "config": {"policy_id": pid}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _alert(w, ch, job_id=None, incident_id=None, condition="failed"):
    from cronsentinel.alerting import tasks
    tasks.send_notification.run(str(w["org"]["id"]), ch, f"dk-{uuid.uuid4().hex}", incident_id,
        {"job_id": job_id or str(uuid.uuid4()), "job_name": "nightly-db-backup", "prev_status": "healthy", "new_status": condition,
         "condition": condition, "severity": "high", "incident_id": incident_id, "execution_id": None, "ts": "t"})


def _tick(minutes):
    from cronsentinel import escalation
    from cronsentinel.db import system_session
    with system_session() as s:
        return escalation.tick(s, datetime.now(UTC) + timedelta(minutes=minutes))


def _run(w):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT id, status, step, cycle, history FROM escalation_runs WHERE org_id=:o ORDER BY created_at DESC LIMIT 1"),
                         {"o": w["org"]["id"]}).first()


def test_schedule_api_reports_who_is_on_call_and_a_timeline(world):
    w = world
    sch = _schedule(w, [w["p"]["Ana"], w["p"]["Ben"]])
    assert sch["on_call_now"]["name"] == "Ana" and sch["timeline"][0]["name"] == "Ana"
    assert {x["name"] for x in sch["timeline"]} == {"Ana", "Ben"}                    # alternates over 14 days
    now = datetime.now(UTC)
    r = w["c"].post(f"/api/v1/oncall/schedules/{sch['id']}/overrides", headers=w["k"]["devops"],
                    json={"user_id": w["p"]["Cy"], "starts_at": (now - timedelta(minutes=1)).isoformat(), "ends_at": (now + timedelta(hours=2)).isoformat()})
    assert r.status_code == 201
    got = w["c"].get(f"/api/v1/oncall/schedules/{sch['id']}", headers=w["k"]["developer"]).json()
    assert got["on_call_now"] == {"user_id": w["p"]["Cy"], "name": "Cy", "source": "override"}


def test_escalates_step_by_step_until_exhausted(world):
    w = world
    sch = _schedule(w, [w["p"]["Ana"]])
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "schedule", "id": sch["id"]}]},
                      {"delay_min": 5, "targets": [{"type": "user", "id": w["p"]["Ben"]}]}])
    _alert(w, _esc_channel(w, pid))
    assert len(paged) == 1 and paged[0]["title"].startswith("[Paging Ana]")          # step 1 at once
    assert "Acknowledge to stop escalation" in paged[0]["body"]
    assert _tick(1) == 0 and len(paged) == 1                                          # nobody pages before the delay
    _tick(6)
    assert len(paged) == 2 and paged[1]["title"].startswith("[Paging Ben]")           # step 2 after 5 minutes
    _tick(12)
    r = _run(w)
    assert r.status == "exhausted" and len(paged) == 2 and [h["paged"] for h in r.history] == [["Ana"], ["Ben"]]


def test_acknowledging_stops_paging_and_acks_the_incident(world):
    w = world
    from cronsentinel.db import system_session
    with system_session() as s:
        inc = str(s.execute(text("INSERT INTO incidents (org_id, title, severity, status) VALUES (:o, 'Backups failing', 'high', 'open') RETURNING id"),
                            {"o": w["org"]["id"]}).scalar())
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "user", "id": w["p"]["Ana"]}]},
                      {"delay_min": 5, "targets": [{"type": "user", "id": w["p"]["Ben"]}]}])
    ch = _esc_channel(w, pid)
    _alert(w, ch, incident_id=inc)
    _alert(w, ch, incident_id=inc)                                                     # same incident: no second run, no double page
    assert len(paged) == 1
    run = _run(w)
    assert w["c"].post(f"/api/v1/oncall/escalations/{run.id}/ack", headers=w["k"]["developer"]).status_code == 200
    _tick(30)
    assert len(paged) == 1 and _run(w).status == "acknowledged"
    with system_session() as s:
        assert s.execute(text("SELECT status::text FROM incidents WHERE id=:i"), {"i": inc}).scalar() == "acknowledged"


def test_incident_acknowledged_elsewhere_or_job_recovery_stops_paging(world):
    w = world
    from cronsentinel import escalation
    from cronsentinel.db import system_session
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "user", "id": w["p"]["Ana"]}]}], repeat=3)
    ch = _esc_channel(w, pid)
    with system_session() as s:
        inc = str(s.execute(text("INSERT INTO incidents (org_id, title, severity, status) VALUES (:o, 'x', 'high', 'open') RETURNING id"), {"o": w["org"]["id"]}).scalar())
    _alert(w, ch, incident_id=inc)
    with system_session() as s:
        s.execute(text("UPDATE incidents SET status='acknowledged', acknowledged_at=now() WHERE id=:i"), {"i": inc})
    _tick(6)
    assert len(paged) == 1 and _run(w).status == "acknowledged"
    job = str(uuid.uuid4())
    _alert(w, ch, job_id=job)
    with system_session() as s:
        assert escalation.resolve_for_job(s, w["org"]["id"], job) == 1
    _tick(6)
    assert len(paged) == 2 and _run(w).status == "resolved"


def test_repeat_and_nobody_on_call(world):
    w = world
    empty = w["c"].post("/api/v1/oncall/schedules", headers=w["k"]["devops"], json={"name": "Future", "tz": "UTC", "layers": [
        {"rotation": "daily", "handoff_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(), "members": [w["p"]["Cy"]]}]}).json()
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "schedule", "id": empty["id"]}, {"type": "user", "id": w["p"]["Ana"]}]}], repeat=1)
    _alert(w, _esc_channel(w, pid))
    _tick(6)
    _tick(12)
    r = _run(w)
    assert len(paged) == 2 and r.status == "exhausted"                                # policy ran twice (repeat 1)
    assert "nobody on call in Future" in r.history[0]["notes"]


def test_validation_roles_and_plan(world):
    w = world
    c, dev, ops = w["c"], w["k"]["developer"], w["k"]["devops"]
    body = {"name": "S", "tz": "UTC", "layers": [{"rotation": "daily", "handoff_at": "2026-10-01T09:00:00Z", "members": [w["p"]["Ana"]]}]}
    assert c.post("/api/v1/oncall/schedules", headers=dev, json=body).status_code == 403
    assert c.post("/api/v1/oncall/schedules", headers=ops, json={**body, "layers": [{**body["layers"][0], "members": [str(uuid.uuid4())]}]}).status_code == 422
    bad = c.post("/api/v1/oncall/schedules", headers=ops, json={**body, "layers": [{**body["layers"][0], "restrict_start": "09:00"}]})
    assert bad.status_code == 422 and "both" in bad.text
    sch = c.post("/api/v1/oncall/schedules", headers=ops, json=body).json()
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "schedule", "id": sch["id"]}]}])
    assert c.delete(f"/api/v1/oncall/schedules/{sch['id']}", headers=ops).status_code == 409         # used by a policy
    _esc_channel(w, pid)
    assert c.delete(f"/api/v1/oncall/policies/{pid}", headers=ops).status_code == 409                # used by a channel
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='developer' WHERE id=:o"), {"o": w["org"]["id"]})
    assert c.post("/api/v1/oncall/schedules", headers=ops, json={**body, "name": "S2"}).status_code == 402


def test_people_and_route_alerts_to_a_policy(world):
    w = world
    names = {p["name"] for p in w["c"].get("/api/v1/oncall/people", headers=w["k"]["developer"]).json()}
    assert {"Ana", "Ben", "Cy"} <= names
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "user", "id": w["p"]["Ana"]}]}])
    r = w["c"].post(f"/api/v1/oncall/policies/{pid}/channel", headers=w["k"]["devops"])
    assert r.status_code == 201 and r.json()["name"].startswith("On call: ")
    _alert(w, r.json()["id"])                                      # the created channel really pages
    assert len(paged) == 1 and paged[0]["title"].startswith("[Paging Ana]")
    assert w["c"].post(f"/api/v1/oncall/policies/{pid}/channel", headers=w["k"]["devops"]).status_code == 409



def test_deleting_a_delivery_channel_in_use_is_refused_not_a_500(world):
    """Found by the router smoke sweep: the channel FK (RESTRICT) surfaced as a 500."""
    w = world
    pid = _policy(w, [{"delay_min": 5, "targets": [{"type": "user", "id": w["p"]["Ana"]}]}])
    r = w["c"].delete(f"/api/v1/alerts/channels/{w['ch']}", headers=w["k"]["devops"])
    assert r.status_code == 409 and "delivers pages" in r.text
    assert w["c"].delete(f"/api/v1/oncall/policies/{pid}", headers=w["k"]["devops"]).status_code == 204
    assert w["c"].delete(f"/api/v1/alerts/channels/{w['ch']}", headers=w["k"]["devops"]).status_code == 204
