"""R65: CronJob annotations and spec.timeZone through the real discovery endpoint."""
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text


@pytest.fixture
def k8s(org):
    from fastapi.testclient import TestClient

    from cronsentinel.auth import hash_secret
    from cronsentinel.db import system_session
    from cronsentinel.ingest_main import app
    key = "csk_" + uuid.uuid4().hex
    with system_session() as s:
        aid = s.execute(text("""INSERT INTO agents (org_id, kind, host_id, name, version, status, key_hash, key_prefix)
            VALUES (:o, 'k8s', :h, :n, '1', 'online', :kh, :kp) RETURNING id"""),
            {"o": org["id"], "h": f"cl-{uuid.uuid4().hex[:6]}", "n": f"prod-{uuid.uuid4().hex[:4]}", "kh": hash_secret(key), "kp": key[:12]}).scalar()
    app.state.js = None
    c = TestClient(app)
    hdr = {"X-Agent-Id": str(aid), "X-Agent-Key": key}

    def send(*cjs):
        r = c.post("/agent/v1/k8s/cronjobs", headers=hdr, json={"cronjobs": list(cjs)})
        assert r.status_code == 200, r.text
    return send


def cj(uid, name="report", ns="billing", schedule="0 2 * * *", tz=None, ann=None, suspend=False):
    return {"UID": uid, "Namespace": ns, "Name": name, "Schedule": schedule, "TimeZone": tz, "Annotations": ann, "Suspend": suspend, "Image": "app:1"}


def job(org_id, uid):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("""SELECT j.id, j.name, j.tz, j.grace_s, j.expected_runtime_s, j.tags, j.assertions, j.description, j.paused,
                                        j.next_expected_at, k.annotation_warnings
                                 FROM k8s_cronjobs k LEFT JOIN jobs j ON j.id = k.job_id WHERE k.org_id=:o AND k.uid=:u"""),
                         {"o": org_id, "u": uid}).first()


def test_annotations_configure_a_new_job_and_time_zone_is_honoured(k8s, org):
    uid = uuid.uuid4().hex
    k8s(cj(uid, tz="Asia/Kolkata", ann={"jobwatch.io/name": f"Billing report {uid[:4]}", "jobwatch.io/grace": "10m",
        "jobwatch.io/expected-runtime": "2h", "jobwatch.io/tags": "billing,prod", "jobwatch.io/run-checks": "duration < 2h"}))
    j = job(org["id"], uid)
    assert j.name.startswith("Billing report") and j.tz == "Asia/Kolkata" and j.grace_s == 600 and j.expected_runtime_s == 7200
    assert j.tags == ["kubernetes", "billing", "prod"] and j.assertions == ["duration < 2h"] and j.annotation_warnings == []
    nx = j.next_expected_at.astimezone(UTC)
    assert (nx.hour, nx.minute) == (20, 30)                 # 02:00 in Kolkata, not 02:00 UTC


def test_present_annotations_win_absent_ones_leave_app_edits(k8s, org):
    from cronsentinel.db import system_session
    uid = uuid.uuid4().hex
    k8s(cj(uid, ann={"jobwatch.io/grace": "5m"}))
    jid = job(org["id"], uid).id
    with system_session() as s:
        s.execute(text("UPDATE jobs SET grace_s=900, expected_runtime_s=1234 WHERE id=:j"), {"j": jid})   # edits in the app
    k8s(cj(uid, ann={"jobwatch.io/grace": "5m"}))
    j = job(org["id"], uid)
    assert j.grace_s == 300 and j.expected_runtime_s == 1234   # annotated value restored; unannotated edit kept


def test_invalid_annotations_warn_and_are_not_applied(k8s, org):
    uid = uuid.uuid4().hex
    k8s(cj(uid, tz="Mars/Olympus", ann={"jobwatch.io/grace": "soon", "jobwatch.io/run-checks": "nonsense"}))
    j = job(org["id"], uid)
    assert j.grace_s == 600 and j.assertions == [] and j.tz == "UTC"
    assert any("not a duration" in w for w in j.annotation_warnings) and any("spec.timeZone" in w for w in j.annotation_warnings)


def test_ignore_skips_new_and_pauses_existing_then_resumes(k8s, org):
    new, existing = uuid.uuid4().hex, uuid.uuid4().hex
    k8s(cj(new, name="scratch", ann={"jobwatch.io/ignore": "true"}))
    assert job(org["id"], new).id is None                    # recorded, not monitored
    k8s(cj(existing, name="etl"))
    assert job(org["id"], existing).paused is False
    k8s(cj(existing, name="etl", ann={"jobwatch.io/ignore": "true"}))
    j = job(org["id"], existing)
    assert j.paused is True and "k8s-ignored" in j.tags
    k8s(cj(existing, name="etl"))
    j = job(org["id"], existing)
    assert j.paused is False and "k8s-ignored" not in j.tags


def test_time_zone_change_drops_slots_generated_for_the_old_zone(k8s, org):
    from cronsentinel.db import system_session
    uid = uuid.uuid4().hex
    k8s(cj(uid, schedule="0 * * * *"))
    jid = job(org["id"], uid).id
    with system_session() as s:
        s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline, state)
                          VALUES (:o, :j, now() + interval '2 hours', now() + interval '2 hours', now() + interval '3 hours', 'pending')"""),
                  {"o": org["id"], "j": jid})
    k8s(cj(uid, schedule="0 * * * *", tz="America/New_York"))
    with system_session() as s:
        left = s.execute(text("SELECT count(*) FROM expected_runs WHERE job_id=:j AND state='pending' AND scheduled_for > now()"), {"j": jid}).scalar()
    assert left == 0 and job(org["id"], uid).tz == "America/New_York"
    assert job(org["id"], uid).next_expected_at > datetime.now(UTC)
