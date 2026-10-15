"""One broken alert rule must only skip itself, not every rule after it
(code review 2026-10-10, finding #5). Rules saved before validation existed can still be bad."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cronsentinel.alerting import engine
from cronsentinel.alerting import rules as R


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class FakeSession:
    def __init__(self, rule_rows):
        self.rule_rows, self.savepoints, self.rolled_back = rule_rows, 0, 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM alert_rules" in sql:
            return _Result(rows=self.rule_rows)
        if sql.startswith("INSERT INTO incidents"):
            return _Result(scalar="inc-1")
        return _Result()

    @contextmanager
    def begin_nested(self):
        self.savepoints += 1
        try:
            yield
        except Exception:
            self.rolled_back += 1
            raise


def _rule(rid, **kw):
    base = dict(id=rid, condition="failed", scope={}, params={}, severity="high", channel_ids=[f"ch-{rid}"],
                business_hours=None, repeat_interval_s=None)
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def harness(monkeypatch):
    sent = []
    job = R.JobCtx(id="job-1", name="nightly-backup", tags=[], environment_id=None, workspace_id="ws", team_id=None)
    monkeypatch.setattr(engine, "_job_ctx", lambda s, org, jid: job)
    monkeypatch.setattr(engine, "_record_change", lambda jid, now: [])
    monkeypatch.setattr(engine, "_in_maintenance", lambda s, org, j, now: False)
    monkeypatch.setattr(engine.C, "signals_for_job", lambda s, org, jid: {})
    monkeypatch.setattr(engine.C, "find_related_incident", lambda s, org, jid, sig: (None, None))
    monkeypatch.setattr(engine.send_notification, "delay", lambda *a: sent.append(a))

    def run(rule_rows):
        sess = FakeSession(rule_rows)

        @contextmanager
        def fake_system_session():
            yield sess
        monkeypatch.setattr(engine, "system_session", fake_system_session)
        engine.handle_status_change({"org_id": "org-1", "job_id": "job-1", "prev_status": "healthy", "new_status": "failed"})
        return sess, sent
    return run


@pytest.mark.parametrize("bad", [
    _rule("bad", business_hours={"tz": "Mars/Olympus"}),
    _rule("bad", business_hours={"start": "9am"}),
    _rule("bad", condition="consecutive_failures", params={"count": "abc"}),
])
def test_bad_rule_does_not_block_later_rules(harness, bad):
    sess, sent = harness([bad, _rule("good")])
    assert [a[1] for a in sent] == ["ch-good"]
    assert sess.savepoints == 2 and sess.rolled_back == 1


def test_good_rules_all_fire(harness):
    sess, sent = harness([_rule("a"), _rule("b")])
    assert sorted(a[1] for a in sent) == ["ch-a", "ch-b"]
    assert sess.rolled_back == 0
