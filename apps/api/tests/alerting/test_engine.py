"""Rule engine I/O layer (alerting/engine.py) against FakeSession + fakeredis.

What these cover: the order of suppressions (unknown → maintenance → scope/condition → business
hours → dedup), incident open vs. append vs. correlate, flapping, auto-resolve on recovery, and
that every matching channel gets one notification.

Not covered here (needs real Postgres): the SQL itself, e.g. review #11 (a correlated incident is
resolved when any one of its jobs recovers, engine.py:71-73) lives entirely in the UPDATE's WHERE
clause and can't be exercised with a fake session.
"""
from datetime import UTC, datetime, timedelta

import pytest

from cronsentinel.alerting import correlation, engine

from .conftest import FakeSession, Row

ORG, JOB = "org-1", "job-1"


class DelayRecorder:
    def __init__(self):
        self.calls = []

    def delay(self, *args):
        self.calls.append(args)


@pytest.fixture
def sent(monkeypatch):
    rec = DelayRecorder()
    monkeypatch.setattr(engine, "send_notification", rec)
    return rec


def rule_row(**kw):
    base = dict(id="r1", condition="failed", scope={}, params={}, severity="high", channel_ids=["c1", "c2"],
                business_hours=None, repeat_interval_s=None)
    base.update(kw)
    return Row(**base)


def _window(scope):
    """A one-off maintenance window open right now, in the shape maintenance.load() selects."""
    now = datetime.now(UTC)
    return Row(id="mw-1", name="mw", scope=scope, starts_at=now - timedelta(hours=1),
               ends_at=now + timedelta(hours=1), rrule=None, tz="UTC")


def session(rules=(), *, job=True, open_incident=None, maintenance=(), last_sent=None, related=()):
    s = FakeSession([
        ("SELECT id, name, tags", [Row(id=JOB, name="backup", tags=["db"], environment_id=None, workspace_id="ws1",
                                       team_id=None, sla_target=None, sla_window=None)] if job else []),
        ("SELECT count(*) FROM executions", 0),
        ("100.0*count(*)", Result_none()),
        ("FROM maintenance_windows", [_window(m) for m in maintenance]),
        ("FROM alert_rules", list(rules)),
        ("FROM notification_ledger", last_sent if last_sent is not None else Result_none()),
        ("SELECT id FROM incidents", [Row(id=open_incident)] if open_incident else []),
        ("SELECT server_id, cluster_id, kind FROM jobs", [Row(server_id=None, cluster_id=None, kind="cron")]),
        ("SELECT host, deploy_version, meta FROM executions", [Row(host="h1", deploy_version="v7", meta={})]),
        ("FROM job_dependencies", []),
        ("SELECT id, affected_job_ids, correlation_signals, started_at FROM incidents", list(related)),
        ("INSERT INTO incidents", "inc-new"),
    ])
    return s


def Result_none():
    from .conftest import Result
    return Result()


def ev(new="failed", prev="healthy", **kw):
    return {"org_id": ORG, "job_id": JOB, "prev_status": prev, "new_status": new, "execution_id": "e1", **kw}


# ---- suppression ----------------------------------------------------------------------------

def test_unknown_visibility_is_suppressed_before_touching_the_db(use_session, fake_redis, sent):
    s = use_session(session([rule_row()]))
    engine.handle_status_change(ev(new_state="unknown", unknown_reason="agent_offline"))
    assert s.calls == [] and sent.calls == []


def test_unknown_job_is_ignored(use_session, fake_redis, sent):
    use_session(session([rule_row()], job=False))
    engine.handle_status_change(ev())
    assert sent.calls == []


def test_maintenance_window_suppresses_rules(use_session, fake_redis, sent):
    s = use_session(session([rule_row()], maintenance=[{}]))
    engine.handle_status_change(ev())
    assert sent.calls == []
    assert not s.statements("FROM alert_rules")


def test_maintenance_scoped_to_other_job_does_not_suppress(use_session, fake_redis, sent):
    use_session(session([rule_row()], maintenance=[{"job_ids": ["someone-else"]}]))
    engine.handle_status_change(ev())
    assert len(sent.calls) == 2


def test_out_of_scope_and_non_matching_rules_send_nothing(use_session, fake_redis, sent):
    use_session(session([rule_row(scope={"job_ids": ["other"]}), rule_row(id="r2", condition="missed")]))
    engine.handle_status_change(ev())
    assert sent.calls == []


def test_outside_business_hours_is_suppressed(use_session, fake_redis, sent):
    use_session(session([rule_row(business_hours={"tz": "UTC", "days": []})]))
    engine.handle_status_change(ev())
    assert sent.calls == []


def test_dedup_suppresses_resend_without_repeat_interval(use_session, fake_redis, sent):
    use_session(session([rule_row()], last_sent=datetime.now(UTC) - timedelta(minutes=5)))
    engine.handle_status_change(ev())
    assert sent.calls == []


def test_repeat_interval_allows_resend(use_session, fake_redis, sent):
    use_session(session([rule_row(repeat_interval_s=60)], last_sent=datetime.now(UTC) - timedelta(minutes=5)))
    engine.handle_status_change(ev())
    assert len(sent.calls) == 2


# ---- incidents + notifications --------------------------------------------------------------

def test_failure_opens_incident_and_notifies_every_channel(use_session, fake_redis, sent):
    s = use_session(session([rule_row()]))
    engine.handle_status_change(ev())
    assert len(s.statements("INSERT INTO incidents")) == 1
    opened = [p for sql, p in s.statements("INSERT INTO incident_events") if "'opened'" in sql]
    assert len(opened) == 1
    assert [c[1] for c in sent.calls] == ["c1", "c2"]
    org, ch, dk, inc, payload = sent.calls[0]
    assert (org, dk, inc) == (ORG, "r1:job-1:failed", "inc-new")
    assert payload["condition"] == "failed" and payload["severity"] == "high" and payload["execution_id"] == "e1"


def test_existing_open_incident_gets_status_change_not_a_new_incident(use_session, fake_redis, sent):
    s = use_session(session([rule_row()], open_incident="inc-old"))
    engine.handle_status_change(ev())
    assert not s.statements("INSERT INTO incidents (")
    assert any("'status_change'" in sql for sql, _ in s.statements("INSERT INTO incident_events"))
    assert {c[3] for c in sent.calls} == {"inc-old"}


def test_related_incident_is_joined_via_correlation(use_session, fake_redis, sent):
    rel = Row(id="inc-rel", affected_job_ids=["job-9"], correlation_signals=[{"host": "h1"}],
              started_at=datetime.now(UTC))
    s = use_session(session([rule_row()], related=[rel]))
    engine.handle_status_change(ev())
    assert not s.statements("INSERT INTO incidents (")
    assert s.statements("UPDATE incidents SET affected_job_ids")
    assert {c[3] for c in sent.calls} == {"inc-rel"}
    assert sent.calls[0][4]["correlated"] == "same host h1"


def test_recovered_rule_notifies_without_opening_incident(use_session, fake_redis, sent):
    s = use_session(session([rule_row(condition="recovered", channel_ids=["c1"])]))
    engine.handle_status_change(ev(new="recovered", prev="failed"))
    assert not s.statements("INSERT INTO incidents (")
    assert len(sent.calls) == 1 and sent.calls[0][3] is None


def test_flapping_replaces_condition_and_titles_incident(use_session, fake_redis, sent):
    now = datetime.now(UTC)
    for sec in (30, 60):
        t = now - timedelta(seconds=sec)
        fake_redis.zadd(f"flap:{JOB}", {t.isoformat(): t.timestamp()})
    s = use_session(session([rule_row()]))
    engine.handle_status_change(ev())
    (sql, params), = s.statements("INSERT INTO incidents (")
    assert params["t"] == "backup is flapping"
    assert sent.calls[0][2] == "r1:job-1:flapping"
    assert sent.calls[0][4]["condition"] == "flapping"


def test_status_changes_are_recorded_for_flap_detection(use_session, fake_redis, sent):
    use_session(session([]))
    engine.handle_status_change(ev())
    assert fake_redis.zcard(f"flap:{JOB}") == 1


# ---- recovery -------------------------------------------------------------------------------

def test_recovery_resolves_incidents_and_closes_oncall_even_in_maintenance(use_session, fake_redis, sent):
    from cronsentinel.alerting import oncall
    fake_redis.sadd(oncall.open_key(ORG, JOB), "pd-1")
    s = use_session(session([], maintenance=[{}]))
    engine.handle_status_change(ev(new="healthy", prev="failed"))
    assert s.statements("UPDATE incidents SET status='resolved'")
    assert len(sent.calls) == 1
    org, ch, dk, inc, payload = sent.calls[0]
    assert ch == "pd-1" and dk.startswith(f"oncall-resolve:{JOB}:pd-1:")
    assert payload["condition"] == "recovered" and payload["auto_resolve"] is True


# ---- review #5: one malformed rule must only skip itself ------------------------------------

def test_bad_business_hours_rule_does_not_block_other_rules(use_session, fake_redis, sent):
    use_session(session([rule_row(id="bad", business_hours={"tz": "Not/AZone"}),
                         rule_row(id="good", channel_ids=["c9"])]))
    engine.handle_status_change(ev())
    assert [c[1] for c in sent.calls] == ["c9"]


def test_bad_count_param_does_not_block_other_rules(use_session, fake_redis, sent):
    use_session(session([rule_row(id="bad", condition="consecutive_failures", params={"count": "abc"}),
                         rule_row(id="good", channel_ids=["c9"])]))
    engine.handle_status_change(ev())
    assert [c[1] for c in sent.calls] == ["c9"]


# ---- correlation helpers --------------------------------------------------------------------

def cand(signals, affected=("job-9",)):
    return Row(id="inc-x", affected_job_ids=list(affected), correlation_signals=signals, started_at=datetime.now(UTC))


@pytest.mark.parametrize("prev,sig,why", [
    ({"host": "h1"}, {"host": "h1"}, "same host h1"),
    ({"cluster_ns": "c:ns"}, {"cluster_ns": "c:ns"}, "same cluster namespace"),
    ({"deploy_version": "v7"}, {"deploy_version": "v7"}, "same deploy v7"),
])
def test_find_related_incident_by_shared_signal(prev, sig, why):
    s = FakeSession([("FROM incidents", [cand([prev])])])
    assert correlation.find_related_incident(s, ORG, JOB, {"upstream": [], "downstream": [], **sig}) == ("inc-x", why)


def test_find_related_incident_by_dependency():
    s = FakeSession([("FROM incidents", [cand([], affected=["up-1"])])])
    assert correlation.find_related_incident(s, ORG, JOB, {"upstream": ["up-1"], "downstream": []})[1] == "upstream dependency failed"
    s = FakeSession([("FROM incidents", [cand([], affected=["down-1"])])])
    assert correlation.find_related_incident(s, ORG, JOB, {"upstream": [], "downstream": ["down-1"]})[1] == "downstream of failing job"


def test_find_related_incident_none_when_nothing_shared():
    s = FakeSession([("FROM incidents", [cand([{"host": "h2", "deploy_version": "v1"}])])])
    assert correlation.find_related_incident(s, ORG, JOB, {"host": "h1", "deploy_version": "v7", "upstream": [], "downstream": []}) == (None, None)


def test_find_related_incident_ignores_empty_signal_values():
    # both sides None must not count as "same host"
    s = FakeSession([("FROM incidents", [cand([{"host": None}])])])
    assert correlation.find_related_incident(s, ORG, JOB, {"host": None, "upstream": [], "downstream": []}) == (None, None)


def test_find_related_incident_accepts_json_string_signals():
    s = FakeSession([("FROM incidents", [cand('[{"host": "h1"}]')])])
    assert correlation.find_related_incident(s, ORG, JOB, {"host": "h1", "upstream": [], "downstream": []})[0] == "inc-x"


@pytest.mark.xfail(strict=True, reason="review (smaller findings): 'same server' and 'infra anomaly' signals are "
                                       "computed but never used to match (correlation.py:39-47)")
def test_find_related_incident_by_same_server():
    s = FakeSession([("FROM incidents", [cand([{"server_id": "srv-1"}])])])
    assert correlation.find_related_incident(s, ORG, JOB, {"server_id": "srv-1", "upstream": [], "downstream": []})[0] == "inc-x"
