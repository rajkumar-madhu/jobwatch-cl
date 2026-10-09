"""R53: systemd timers discovered by the agent get cron schedules when cron can express them exactly."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.integration.test_agent_events import agent, ingest  # noqa: F401  (fixtures)

CAL = "{ OnCalendar=Mon..Fri *-*-* 09:00:00 Asia/Kolkata ; next_elapse=Mon 2026-10-05 03:30:00 UTC }"


def _discover(ingest, agent, jobs):  # noqa: F811
    r = ingest.post("/agent/v1/discovery", json={"jobs": jobs}, headers={"X-Agent-Id": agent["id"], "X-Agent-Key": agent["key"]})
    assert r.status_code == 200, r.text
    return r.json()


def _timer(fp, sched, name="renew-certs", slack=60):
    t = {"fingerprint": fp, "kind": "systemd", "name": name, "schedule": sched, "command": "/usr/bin/certbot renew",
         "user": "root", "source": f"{name}.timer"}
    if slack is not None:
        t["timer_slack_s"] = slack
    return t


def _job(org_id, fp):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT schedule_expr, tz, grace_s, next_expected_at, generated_through FROM jobs WHERE org_id=:o AND fingerprint=:f"),
                         {"o": org_id, "f": fp}).first()


def test_new_timer_gets_cron_with_its_timezone(ingest, agent, org):  # noqa: F811
    fp = f"fp-{uuid.uuid4().hex}"
    _discover(ingest, agent, [_timer(fp, CAL)])
    j = _job(org["id"], fp)
    assert (j.schedule_expr, j.tz) == ("0 9 * * 1,2,3,4,5", "Asia/Kolkata")
    # next expected run is 09:00 India time on a weekday = 03:30 UTC
    nx = j.next_expected_at.astimezone(UTC)
    assert (nx.hour, nx.minute) == (3, 30) and nx.weekday() < 5


def test_interval_timer_gets_period_cron_and_extra_grace(ingest, agent, org):  # noqa: F811
    fp = f"fp-{uuid.uuid4().hex}"
    _discover(ingest, agent, [_timer(fp, "{ OnUnitActiveSec=15min ; next_elapse=x }", "queue-drain")])
    j = _job(org["id"], fp)
    assert j.schedule_expr == "*/15 * * * *" and j.grace_s >= 900


def test_untranslatable_timer_stays_unscheduled(ingest, agent, org):  # noqa: F811
    fp = f"fp-{uuid.uuid4().hex}"
    _discover(ingest, agent, [_timer(fp, "{ OnCalendar=*-*-* 03:00:30 ; next_elapse=x }")])
    assert _job(org["id"], fp).schedule_expr is None


def test_existing_unscheduled_timer_is_filled_from_now_but_manual_schedule_wins(ingest, agent, org):  # noqa: F811
    from cronsentinel.db import system_session
    fp_empty, fp_manual = f"fp-{uuid.uuid4().hex}", f"fp-{uuid.uuid4().hex}"
    with system_session() as s:
        for fp, sched in ((fp_empty, None), (fp_manual, "30 7 * * *")):
            s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, source, fingerprint, heartbeat_token_hash, heartbeat_token_hint,
                    schedule_expr, created_at, status) VALUES (:o, :w, :n, 'systemd', 'agent', :f, :h, 'xxxx', :sch, now() - interval '30 days', 'unknown')"""),
                      {"o": org["id"], "w": org["ws"], "n": f"t-{fp[-6:]}", "f": fp, "h": uuid.uuid4().hex, "sch": sched})
    _discover(ingest, agent, [_timer(fp_empty, CAL, "a"), _timer(fp_manual, CAL, "b")])
    filled = _job(org["id"], fp_empty)
    assert filled.schedule_expr == "0 9 * * 1,2,3,4,5"
    # no backlog: generation starts now, not 30 days ago (which would mark weeks of runs "missed")
    assert filled.generated_through > datetime.now(UTC) - timedelta(minutes=5)
    assert _job(org["id"], fp_manual).schedule_expr == "30 7 * * *"     # a person's choice is never overwritten


def test_native_cron_line_still_refreshes(ingest, agent, org):  # noqa: F811
    fp = f"fp-{uuid.uuid4().hex}"
    _discover(ingest, agent, [{"fingerprint": fp, "kind": "cron", "name": "nightly", "schedule": "0 2 * * *", "command": "/opt/b.sh", "user": "root", "source": "/etc/crontab"}])
    _discover(ingest, agent, [{"fingerprint": fp, "kind": "cron", "name": "nightly", "schedule": "0 3 * * *", "command": "/opt/b.sh", "user": "root", "source": "/etc/crontab"}])
    assert _job(org["id"], fp).schedule_expr == "0 3 * * *"


def test_generator_creates_slots_only_from_now(ingest, agent, org):  # noqa: F811
    from cronsentinel.db import system_session
    from cronsentinel.workers.schedule_generator import tick
    fp = f"fp-{uuid.uuid4().hex}"
    with system_session() as s:
        s.execute(text("""INSERT INTO jobs (org_id, workspace_id, name, kind, source, fingerprint, heartbeat_token_hash, heartbeat_token_hint,
                created_at, status) VALUES (:o, :w, 'old-timer', 'systemd', 'agent', :f, :h, 'xxxx', now() - interval '30 days', 'unknown')"""),
                  {"o": org["id"], "w": org["ws"], "f": fp, "h": uuid.uuid4().hex})
    _discover(ingest, agent, [_timer(fp, "{ OnCalendar=*-*-* *:00:00 ; next_elapse=x }", "hourly-thing")])
    with system_session() as s:
        tick(s)
        past = s.execute(text("""SELECT count(*) FROM expected_runs er JOIN jobs j ON j.id = er.job_id
            WHERE j.fingerprint=:f AND er.scheduled_for < now() - interval '1 hour'"""), {"f": fp}).scalar()
    assert past == 0



def test_randomized_delay_widens_grace(ingest, agent, org):  # noqa: F811
    """certbot.timer: OnCalendar=*-*-* 00,12:00:00 with RandomizedDelaySec=12h."""
    fp = f"fp-{uuid.uuid4().hex}"
    _discover(ingest, agent, [_timer(fp, "{ OnCalendar=*-*-* 00,12:00:00 ; next_elapse=x }", "certbot", slack=43200 + 60)])
    j = _job(org["id"], fp)
    assert j.schedule_expr == "0 0,12 * * *" and j.grace_s >= 43260


def test_agent_too_old_to_report_slack_leaves_timer_unscheduled(ingest, agent, org):  # noqa: F811
    fp = f"fp-{uuid.uuid4().hex}"
    _discover(ingest, agent, [_timer(fp, CAL, slack=None)])
    assert _job(org["id"], fp).schedule_expr is None
