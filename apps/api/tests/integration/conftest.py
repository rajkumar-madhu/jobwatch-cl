"""R5 integration harness. Needs DATABASE_URL pointing at a Postgres with migrations at head.
Everything here talks to the real DB through the same session helpers the app uses; NATS and
Celery are not required (workers' tick() functions are called directly, deliveries are patched)."""
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs postgres (DATABASE_URL)")


@pytest.fixture(scope="session", autouse=True)
def _require_db():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs postgres")


@pytest.fixture(autouse=True)
def _healthy_platform():
    """R39: every integration test starts with all workers freshly heard from and no recorded gaps.
    This used to live in test_monitoring_gaps.py only, so the stale heartbeats and gap rows that
    file (and any test that ran the reconciler across minutes) left behind leaked into later files:
    test_slot_lifecycle / test_unknown_suppression then saw their overdue slots as `unobserved`
    instead of `missed` and failed depending on run order and wall-clock — the R38 "flake"."""
    from cronsentinel.db import system_session
    with system_session() as s:
        s.execute(text("DELETE FROM monitoring_gaps"))
        s.execute(text("""INSERT INTO platform_heartbeats (service, last_seen) VALUES ('schedule-generator', now()), ('reconciler', now()), ('ingest', now())
            ON CONFLICT (service) DO UPDATE SET last_seen = now()"""))
    # R47: per-IP rate-limit windows outlive a test (60 s) and every TestClient request comes from
    # "testclient", so counters from one test (or the previous run) throttled the next.
    try:
        import redis as _redis

        from cronsentinel.config import settings
        r = _redis.from_url(settings.redis_url)
        for pat in ("hbmiss:*", "enroll:*", "rl:*", "hb:*"):   # hb:* — resolve cache can outlive SQL-deleted test orgs
            for k in r.scan_iter(pat):
                r.delete(k)
    except Exception:
        pass
    yield


@pytest.fixture
def org():
    from cronsentinel.db import system_session
    oid = uuid.uuid4()
    with system_session() as s:
        s.execute(text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :n)"), {"id": oid, "n": f"it-{oid.hex[:8]}"})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'w') RETURNING id"), {"o": oid}).scalar()
    yield {"id": oid, "ws": ws}
    with system_session() as s:
        # execution_events has no FK to organizations (append-only ledger); clean it explicitly
        s.execute(text("DELETE FROM execution_events WHERE org_id=:id"), {"id": oid})
        s.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": oid})


@pytest.fixture
def make_job(org):
    from cronsentinel.db import system_session

    def _mk(schedule="*/5 * * * *", grace_s=60, expected_runtime_s=30, created_ago=timedelta(minutes=30), **kw):
        with system_session() as s:
            return s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token_hash, heartbeat_token_hint, schedule_expr, tz, grace_s,
                expected_runtime_s, created_at, status) VALUES (:o, :w, :n, 'cron', encode(sha256(convert_to(CAST(:t AS text),'UTF8')),'hex'), right(CAST(:t AS text),4), :sch, 'UTC', :g, :r, :c, 'unknown') RETURNING id"""),
                {"o": org["id"], "w": org["ws"], "n": kw.get("name", "it-job"), "t": uuid.uuid4().hex, "sch": schedule, "g": grace_s,
                 "r": expected_runtime_s, "c": datetime.now(UTC) - created_ago}).scalar()
    return _mk


def now():
    return datetime.now(UTC)
