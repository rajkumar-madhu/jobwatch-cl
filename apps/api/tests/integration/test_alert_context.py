"""R55: alerts say which server, application, environment and why — on every channel."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from cronsentinel.alerting import context as C


@pytest.fixture
def world(org):
    """A job on server prod-db-01 in 'production', team 'Data', with a failed run that broke a check."""
    from cronsentinel.db import system_session
    o = org["id"]
    with system_session() as s:
        srv = s.execute(text("INSERT INTO servers (org_id, hostname) VALUES (:o, 'prod-db-01') RETURNING id"), {"o": o}).scalar()
        env = s.execute(text("INSERT INTO environments (org_id, workspace_id, name) VALUES (:o, :w, 'production') RETURNING id"), {"o": o, "w": org["ws"]}).scalar()
        team = s.execute(text("INSERT INTO teams (org_id, name) VALUES (:o, 'Data') RETURNING id"), {"o": o}).scalar()
        jid = s.execute(text("""INSERT INTO jobs (org_id, workspace_id, environment_id, team_id, server_id, name, kind, schedule_expr, tz, tags,
                heartbeat_token_hash, heartbeat_token_hint, status)
                VALUES (:o, :w, :e, :t, :s, :n, 'cron', '0 2 * * *', 'Asia/Kolkata', ARRAY['prod','db'], :h, 'xxxx', 'failed') RETURNING id"""),
                            {"o": o, "w": org["ws"], "e": env, "t": team, "s": srv, "n": f"nightly-db-backup-{uuid.uuid4().hex[:4]}", "h": uuid.uuid4().hex}).scalar()
        eid = f"ex-{uuid.uuid4().hex[:8]}"
        start = datetime(2026, 10, 4, 20, 30, tzinfo=UTC)      # 02:00 in Kolkata
        s.execute(text("""INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, agent_ts_start, agent_ts_end, duration_ms, exit_code, host, metrics, meta)
                VALUES (:id, :o, :j, 'failed', :st, :st, :en, 252000, 0, 'prod-db-01', '{"size_bytes": 0}', '{"assertion_failures": ["metric.size_bytes > 0 (was 0)"]}')"""),
                  {"id": eid, "o": o, "j": jid, "st": start, "en": start + timedelta(seconds=252)})
        s.execute(text("INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) VALUES (:o, :e, 'stderr', 0, :c)"),
                  {"o": o, "e": eid, "c": "starting\npg_dump: error: connection to server failed: password=hunter2secret\n"})
    return {"org": o, "job": str(jid), "exec": eid}


def _payload(w, **kw):
    return {"job_id": w["job"], "job_name": "x", "prev_status": "healthy", "new_status": "failed", "condition": "failed",
            "severity": "high", "incident_id": "inc-1", "execution_id": w["exec"], "ts": "t", **kw}


def test_subject_and_body_carry_where_and_why(world):
    from cronsentinel.db import system_session
    p = _payload(world)
    with system_session() as s:
        c = C.build(s, world["org"], p)
    subj = C.subject(p, c)
    assert subj.startswith("✕ [HIGH] FAILED · nightly-db-backup-")
    assert "on prod-db-01 (production)" in subj and "run check failed: metric.size_bytes > 0 (was 0)" in subj
    body = dict(C.details(p, c))
    assert body["Server"] == "prod-db-01" and body["Environment"] == "production" and body["Team"] == "Data"
    assert body["Tags"] == "prod, db" and "0 2 * * *, Asia/Kolkata" in body["Schedule"]
    assert body["Started"] == "2026-10-05 02:00 IST" and body["Duration"] == "4m 12s" and body["Exit code"] == "0"
    assert body["Metrics"] == "size_bytes=0" and body["Open in JobWatch"].endswith(f"/jobs/{world['job']}")
    assert "hunter2secret" not in body["Last error"] and body["Last error"].startswith("pg_dump: error")   # redacted


def test_missed_run_names_the_expected_slot(world):
    from cronsentinel.db import system_session
    slot = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=20)
    with system_session() as s:
        s.execute(text("""INSERT INTO expected_runs (org_id, job_id, scheduled_for, grace_until, deadline, state)
                          VALUES (:o, :j, :t, :t, :t, 'missed')"""), {"o": world["org"], "j": world["job"], "t": slot})
        p = _payload(world, condition="missed", new_status="missed", execution_id=None)
        c = C.build(s, world["org"], p)
    subj = C.subject(p, c)
    assert "MISSED" in subj and "no run started for" in subj and "IST" in subj and "on prod-db-01" in subj


def test_subject_is_bounded_and_test_sends_still_work():
    long = {"job_id": None, "job_name": "j" * 400, "prev_status": "a", "new_status": "failed", "condition": "failed", "severity": "low"}
    assert len(C.subject(long, {})) <= C.SUBJECT_MAX
    assert C.subject({**long, "job_name": "test-job", "test": True}, {}).startswith("✕ [LOW] FAILED · test-job")


def test_every_channel_gets_the_context(world, monkeypatch):
    """Through the real notifier task: the sender receives the rich subject and the context."""
    from cronsentinel.alerting import tasks
    from cronsentinel.crypto import encrypt_json
    from cronsentinel.db import system_session
    seen = {}
    monkeypatch.setitem(tasks._SENDERS, "webhook", lambda cfg, title, body, p: seen.update(title=title, body=body, ctx=p.get("context")))
    with system_session() as s:
        ch = s.execute(text("INSERT INTO notification_channels (org_id, kind, name, config_enc, rate_per_min) VALUES (:o,'webhook','w',:c,30) RETURNING id"),
                       {"o": world["org"], "c": encrypt_json({"url": "https://example.test/hook"})}).scalar()
    tasks.send_notification.run(world["org"], str(ch), f"dk-{uuid.uuid4().hex}", None, _payload(world))
    assert "on prod-db-01 (production)" in seen["title"] and "Team: Data" in seen["body"]
    assert seen["ctx"]["server"] == "prod-db-01" and seen["ctx"]["checks"] == ["metric.size_bytes > 0 (was 0)"]


def test_slack_teams_discord_layouts(monkeypatch):
    from cronsentinel.alerting import tasks
    posted = []
    monkeypatch.setattr(tasks.netguard, "post", lambda url, **kw: posted.append(kw["json"]) or type("R", (), {"raise_for_status": lambda self: None})())
    p = {"job_name": "j", "prev_status": "healthy", "new_status": "failed", "condition": "failed", "severity": "high",
         "context": {"job": "j", "host": "web-1", "environment": "staging", "exit_code": 3}}
    tasks._send_slack({"webhook_url": "https://h"}, "t", "b", p)
    tasks._send_teams({"webhook_url": "https://h"}, "t", "b", p)
    tasks._send_discord({"webhook_url": "https://h"}, "t", "b", p)
    slack, teams, discord = posted
    assert {"title": "Server", "value": "web-1", "short": True} in slack["attachments"][0]["fields"]
    assert {"name": "Environment", "value": "staging"} in teams["sections"][0]["facts"]
    assert {"name": "Exit code", "value": "3", "inline": True} in discord["embeds"][0]["fields"]
