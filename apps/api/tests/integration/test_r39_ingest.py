"""R39: execution-id isolation, URL-mode start/success pairing, client timestamp sanity, token cache."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


def now():
    return datetime.now(UTC)


def _proc(ev):
    from cronsentinel.db import system_session
    from cronsentinel.processor import process
    with system_session() as s:
        return process(s, {"server_ts": now().isoformat(), "meta": {}, **ev})


def _execs(job_id):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT id, status::text, org_id FROM executions WHERE job_id=:j ORDER BY server_received_ts"), {"j": job_id}).all()


@pytest.fixture
def second_org():
    from cronsentinel.db import system_session
    oid = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :n)"), {"id": oid, "n": f"it2-{oid.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w') RETURNING id"), {"o": oid}).scalar()
        j = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token_hash, heartbeat_token_hint, status)
            VALUES (:o, :w, 'other', 'heartbeat', encode(sha256(convert_to(CAST(:t AS text),'UTF8')),'hex'), right(CAST(:t AS text),4), 'unknown') RETURNING id"""), {"o": oid, "w": ws, "t": uuid.uuid4().hex}).scalar()
    yield {"id": oid, "job": j}
    with system_session() as s:
        s.execute(text("DELETE FROM execution_events WHERE org_id=:id"), {"id": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": oid})


def test_url_mode_start_then_success_is_one_execution(make_job, org):
    j = make_job(schedule=None) if False else make_job()
    _proc({"org_id": org["id"], "job_id": j, "kind": "start"})
    _proc({"org_id": org["id"], "job_id": j, "kind": "success"})
    ex = _execs(j)
    assert len(ex) == 1, ex
    assert ex[0].status == "success"


def test_url_mode_fail_without_start_still_records(make_job, org):
    j = make_job()
    _proc({"org_id": org["id"], "job_id": j, "kind": "fail"})
    assert [e.status for e in _execs(j)] == ["failed"]


def test_same_execution_id_in_two_orgs_does_not_collide(make_job, org, second_org):
    """Org B reusing org A's execution id used to be dropped as a duplicate (and on `start`, could
    UPDATE A's row). Both runs must now be recorded, each in its own org, A's untouched."""
    ja = make_job()
    eid = f"backup-{uuid.uuid4().hex[:6]}"
    ts = now().isoformat()
    _proc({"org_id": org["id"], "job_id": ja, "kind": "start", "execution_id": eid, "sequence": 0, "agent_ts": ts})
    _proc({"org_id": org["id"], "job_id": ja, "kind": "success", "execution_id": eid, "sequence": 1, "agent_ts": ts})
    res = _proc({"org_id": str(second_org["id"]), "job_id": second_org["job"], "kind": "start", "execution_id": eid, "sequence": 0, "agent_ts": ts})
    assert res is not None, "second org's event was dropped as a duplicate"
    _proc({"org_id": str(second_org["id"]), "job_id": second_org["job"], "kind": "fail", "execution_id": eid, "sequence": 1, "agent_ts": ts})
    a, b = _execs(ja), _execs(second_org["job"])
    assert [(e.id, e.status) for e in a] == [(eid, "success")]
    assert len(b) == 1 and b[0].status == "failed" and b[0].id != eid


def test_naive_and_absurd_agent_ts_do_not_crash(make_job, org):
    j = make_job()
    _proc({"org_id": org["id"], "job_id": j, "kind": "success", "execution_id": f"n-{uuid.uuid4().hex}",
           "agent_ts": datetime.now(UTC).replace(tzinfo=None).isoformat()})     # naive
    _proc({"org_id": org["id"], "job_id": j, "kind": "success", "execution_id": f"o-{uuid.uuid4().hex}",
           "agent_ts": "1970-01-01T00:00:00Z"})                                  # absurd → server time
    _proc({"org_id": org["id"], "job_id": j, "kind": "success", "execution_id": f"g-{uuid.uuid4().hex}",
           "agent_ts": "not-a-date"})
    assert len(_execs(j)) == 3
    from cronsentinel.db import system_session
    with system_session() as s:
        oldest = s.execute(text("SELECT min(scheduled_ts) FROM executions WHERE job_id=:j"), {"j": j}).scalar()
    assert oldest > now() - timedelta(days=8)


def test_unknown_event_kind_is_ignored_not_treated_as_failure(make_job, org):
    j = make_job()
    assert _proc({"org_id": org["id"], "job_id": j, "kind": "k8s_event", "execution_id": f"k-{uuid.uuid4().hex}"}) is None
    assert _execs(j) == []


# ---- API-level checks --------------------------------------------------------------------------

@pytest.fixture
def api(org):
    """TestClient + one API key per role for `org`."""
    from fastapi.testclient import TestClient

    from cronsentinel.auth import ROLE_ORDER, generate_api_key, hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.main import app
    keys = {}
    with system_session() as s:
        for role in ROLE_ORDER:
            prefix, raw = generate_api_key()
            s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,:n,:p,:h,:r)"),
                      {"o": org["id"], "n": f"k-{role}", "p": prefix, "h": hash_secret(raw), "r": role})
            keys[role] = {"X-API-Key": raw}
    from cronsentinel.ingest_main import app as ingest_app
    ingest_app.state.js = None   # inline processing, no broker
    return _Both(TestClient(app, raise_server_exceptions=False), TestClient(ingest_app, raise_server_exceptions=False)), keys


class _Both:
    """API calls go to the API app, /ping, /heartbeat and /agent/* to the ingest app (as deployed)."""
    def __init__(self, api, ingest):
        self.api, self.ingest = api, ingest

    def _pick(self, path):
        return self.ingest if path.startswith(("/ping", "/heartbeat", "/agent/", "/api/v1/heartbeat")) else self.api

    def __getattr__(self, verb):
        return lambda path, *a, **kw: getattr(self._pick(path), verb)(path, *a, **kw)


def _ping(c, tok):
    return c.get(f"/ping/{tok}")


def test_pause_and_rotate_take_effect_immediately(api, org):
    c, k = api
    j = c.post("/api/v1/jobs", headers=k["devops"], json={"workspace_id": str(org["ws"]), "name": f"r39-{uuid.uuid4().hex[:6]}"}).json()
    tok = j["heartbeat_token"]
    assert _ping(c, tok).status_code == 200            # warms the resolve cache
    assert c.patch(f"/api/v1/jobs/{j['id']}", headers=k["devops"], json={"paused": True}).status_code == 200
    assert _ping(c, tok).status_code == 404, "paused job still accepted from cache"
    assert c.patch(f"/api/v1/jobs/{j['id']}", headers=k["devops"], json={"paused": False}).status_code == 200
    assert _ping(c, tok).status_code == 200, "un-paused job still rejected from negative cache"
    new = c.post(f"/api/v1/jobs/{j['id']}/rotate-token", headers=k["devops"]).json()["heartbeat_token"]
    assert new != tok
    assert _ping(c, tok).status_code == 404 and _ping(c, new).status_code == 200


def test_job_cannot_reference_another_orgs_workspace(api, second_org):
    c, k = api
    from cronsentinel.db import system_session
    with system_session() as s:
        foreign_ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o"), {"o": second_org["id"]}).scalar()
    r = c.post("/api/v1/jobs", headers=k["devops"], json={"workspace_id": str(foreign_ws), "name": "x"})
    assert r.status_code == 422


def test_invalid_timezone_is_a_400_not_a_500(api, org):
    c, k = api
    r = c.post("/api/v1/jobs", headers=k["devops"], json={"workspace_id": str(org["ws"]), "name": "tz", "schedule_expr": "* * * * *", "tz": "Mars/Olympus"})
    assert r.status_code == 422
    assert c.post("/api/v1/jobs/schedule/preview", headers=k["viewer"], params={"expr": "* * * * *", "tz": "Nope/Nope"}).status_code == 400


def test_viewer_cannot_export_org(api):
    c, k = api
    assert c.get("/api/v1/org/export", headers=k["viewer"]).status_code == 403
    assert c.get("/api/v1/org/export", headers=k["admin"]).status_code == 200


def test_api_key_role_is_validated(api):
    c, k = api
    assert c.post("/api/v1/api-keys", headers=k["admin"], json={"name": "x", "role": "superuser"}).status_code == 400
    assert c.post("/api/v1/api-keys", headers=k["admin"], json={"name": "x", "role": "owner"}).status_code == 400
    assert c.post("/api/v1/api-keys", headers=k["admin"], json={"name": "x", "role": "sre"}).status_code == 201


def test_bootstrap_token_is_single_use(api):
    c, k = api
    tok = c.post("/api/v1/agents/bootstrap-token", headers=k["devops"], json={}).json()["token"]
    body = {"bootstrap_token": tok, "name": "h", "host_id": f"h-{uuid.uuid4().hex}"}
    assert c.post("/agent/v1/enroll", json=body).status_code == 200
    assert c.post("/agent/v1/enroll", json={**body, "host_id": f"h-{uuid.uuid4().hex}"}).status_code == 401


def test_bad_agent_id_header_is_401_not_500(api):
    c, _ = api
    assert c.post("/agent/v1/heartbeat", headers={"X-Agent-Id": "not-a-uuid", "X-Agent-Key": "x"}, json={}).status_code == 401


def test_heartbeat_body_is_bounded(api, org):
    c, k = api
    j = c.post("/api/v1/jobs", headers=k["devops"], json={"workspace_id": str(org["ws"]), "name": f"b-{uuid.uuid4().hex[:6]}"}).json()
    r = c.post(f"/api/v1/heartbeat/{j['heartbeat_token']}", json={"status": "success", "meta": {"x": "y" * 20000}})
    assert r.status_code == 422


# ---- R40: PATCH semantics and rescheduling -------------------------------------------------------

def _mk(c, k, org, **extra):
    r = c.post("/api/v1/jobs", headers=k["devops"], json={"workspace_id": str(org["ws"]), "name": f"r40-{uuid.uuid4().hex[:6]}", **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_patch_can_change_schedule(api, org):
    """Changing a schedule used to 500: the audit payload carried a datetime json.dumps can't encode."""
    c, k = api
    j = _mk(c, k, org, schedule_expr="0 3 * * *")
    r = c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={"schedule_expr": "*/5 * * * *"})
    assert r.status_code == 200, r.text
    assert r.json()["schedule_expr"] == "*/5 * * * *"


def test_patch_null_clears_nullable_fields_and_rejects_required_ones(api, org):
    c, k = api
    j = _mk(c, k, org, schedule_expr="0 3 * * *", expected_runtime_s=60, tags=["a"])
    r = c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={"schedule_expr": None, "expected_runtime_s": None, "tags": None})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["schedule_expr"] is None and out["expected_runtime_s"] is None and out["tags"] == []
    assert c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={"tz": None}).status_code == 422
    assert c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={}).status_code == 400
    # omitted fields are untouched
    r = c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={"grace_s": 120})
    assert r.json()["grace_s"] == 120 and r.json()["tags"] == []


def test_schedule_change_replaces_future_slots(api, org):
    """Old future slots must not survive a reschedule (they would go `missed` and alert), and the
    new schedule must be materialised over the horizon the old one had already covered."""
    c, k = api
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import tick
    j = _mk(c, k, org, schedule_expr="7,23,41 * * * *")
    with system_session() as s:
        tick(s)
        before = s.execute(text("SELECT count(*) FROM expected_runs WHERE job_id=:j AND scheduled_for > now()"), {"j": j["id"]}).scalar()
    assert before >= 1
    assert c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={"schedule_expr": "*/30 * * * *"}).status_code == 200
    with system_session() as s:
        tick(s)
        mins = {r.m for r in s.execute(text("SELECT EXTRACT(minute FROM scheduled_for)::int AS m FROM expected_runs WHERE job_id=:j AND scheduled_for > now()"), {"j": j["id"]})}
    assert mins and mins <= {0, 30}, f"stale or missing slots after reschedule: {mins}"
    assert c.patch(f"/api/v1/jobs/{j['id']}", headers=k["developer"], json={"schedule_expr": None}).status_code == 200
    with system_session() as s:
        tick(s)
        left = s.execute(text("SELECT count(*) FROM expected_runs WHERE job_id=:j AND scheduled_for > now() AND state='pending'"), {"j": j["id"]}).scalar()
    assert left == 0, "clearing the schedule must not leave slots that will later be marked missed"


# ---- R41: ping tokens are stored hashed ----------------------------------------------------------

def test_ping_token_is_shown_once_and_never_stored(api, org):
    c, k = api
    j = _mk(c, k, org)
    tok = j["heartbeat_token"]
    assert tok and j["heartbeat_token_hint"] == tok[-4:]
    from cronsentinel.db import system_session
    with system_session() as s:
        row = s.execute(text("SELECT to_jsonb(jobs.*)::text AS t FROM jobs WHERE id=:id"), {"id": j["id"]}).scalar()
    assert tok not in row, "plaintext ping token found in the jobs row"
    # reads never return it again
    assert c.get(f"/api/v1/jobs/{j['id']}", headers=k["viewer"]).json()["heartbeat_token"] is None
    assert all(x["heartbeat_token"] is None for x in c.get("/api/v1/jobs", headers=k["viewer"]).json()["items"])
    # and the URL still works
    assert _ping(c, tok).status_code == 200
    import redis as _redis
    from cronsentinel.config import settings
    r = _redis.from_url(settings.redis_url)
    assert not r.keys(f"*{tok}*"), "plaintext token found in a Redis key (resolve cache or rate limiter)"



def test_many_valid_tokens_from_one_host_are_not_rate_limited(api, org):
    """R47: 100 jobs on one server, first pings after a cache flush, must all be accepted; only
    unknown tokens count toward the per-IP guessing limit."""
    c, k = api
    import redis as _redis
    from cronsentinel import pingtokens
    from cronsentinel.config import settings
    from cronsentinel.db import system_session
    toks = []
    with system_session() as s:
        s.execute(text("UPDATE organizations SET plan='enterprise' WHERE id=:o"), {"o": org["id"]})
        for i in range(100):
            raw, h, hint = pingtokens.new_token(); toks.append(raw)
            s.execute(text("INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token_hash, heartbeat_token_hint, status) VALUES (:o,:w,:n,'heartbeat',:h,:hint,'unknown')"),
                      {"o": org["id"], "w": org["ws"], "n": f"many-{i}-{uuid.uuid4().hex[:4]}", "h": h, "hint": hint})
    r = _redis.from_url(settings.redis_url)
    for key in r.scan_iter("hbmiss:*"):
        r.delete(key)
    codes = [_ping(c, t).status_code for t in toks]
    assert codes.count(200) == 100, {x: codes.count(x) for x in set(codes)}
    # guessing is still limited
    run = uuid.uuid4().hex[:8]   # unique per run: repeats would be answered by the negative cache (no DB, not counted)
    bad = [_ping(c, f"guess-{run}-{i}").status_code for i in range(70)]
    assert bad.count(429) >= 9 and 404 in bad


def test_per_token_rate_limit_still_applies_after_pipelining(api, org, monkeypatch):
    """R47: the limiter moved into one pipelined round trip with the resolve cache; it must still bite."""
    c, k = api
    from cronsentinel.config import settings
    monkeypatch.setattr(settings, "ingest_rate_per_min", 3)
    j = c.post("/api/v1/jobs", headers=k["devops"], json={"workspace_id": str(org["ws"]), "name": f"rl-{uuid.uuid4().hex[:6]}"}).json()
    codes = [_ping(c, j["heartbeat_token"]).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429], codes
