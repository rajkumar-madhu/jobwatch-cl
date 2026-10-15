"""Notification task (alerting/tasks.py): channel lookup, per-channel rate limit, ledger, on-call
bookkeeping and retry. Runs the Celery task eagerly with `.apply()`, so retries execute inline
(no broker, countdown ignored) and their effect on the rate-limit counter is observable."""
import pytest

from cronsentinel import netguard
from cronsentinel.alerting import oncall, tasks

from .conftest import FakeSession, Row

ORG, CH = "org-1", "ch-1"


class FlakySender:
    """Fails the first `fail_times` calls with `exc`, then succeeds."""

    def __init__(self, fail_times=0, exc=None):
        self.fail_times, self.exc, self.calls = fail_times, exc or RuntimeError("boom"), []

    def __call__(self, cfg, title, body, p):
        self.calls.append(p)
        if len(self.calls) <= self.fail_times:
            raise self.exc


@pytest.fixture
def channel(monkeypatch, use_session, fake_redis):
    """Install a FakeSession serving one channel; returns a function to (re)configure it."""
    monkeypatch.setattr(tasks, "decrypt_json", lambda enc: {"webhook_url": "https://hooks.example/x"})
    monkeypatch.setattr(tasks.alert_context, "build", lambda s, org, p: {})

    def make(kind="slack", rate=10, enabled=True, sender=None):
        sender = sender or FlakySender()
        monkeypatch.setitem(tasks._SENDERS, kind, sender)
        s = use_session(FakeSession([
            ("FROM notification_channels", [Row(kind=kind, config_enc=b"x", rate_per_min=rate, enabled=enabled)] if enabled is not None else []),
        ]))
        return s, sender

    return make


def payload(**kw):
    p = {"job_id": "j1", "job_name": "backup", "prev_status": "healthy", "new_status": "failed",
         "condition": "failed", "severity": "high", "incident_id": "i1", "execution_id": "e1", "ts": "2026-10-12T00:00:00Z"}
    p.update(kw)
    return p


def run(dedup="r1:j1:failed", incident="i1", p=None):
    return tasks.send_notification.apply(args=(ORG, CH, dedup, incident, p or payload()))


def ledger(s):
    return [p["st"] if "st" in p else "rate_limited" for _, p in s.statements("INSERT INTO notification_ledger")]


def test_missing_or_disabled_channel_sends_nothing(channel):
    s, sender = channel(enabled=False)
    run()
    assert sender.calls == [] and ledger(s) == []


def test_success_writes_sent_ledger_and_incident_event(channel):
    s, sender = channel()
    r = run()
    assert r.successful()
    assert len(sender.calls) == 1 and ledger(s) == ["sent"]
    assert s.statements("UPDATE incidents SET last_notified_at")
    assert any("'notified'" in sql for sql, _ in s.statements("INSERT INTO incident_events"))


def test_no_incident_means_no_incident_writes(channel):
    s, _ = channel()
    run(incident=None)
    assert not s.statements("UPDATE incidents")


def test_rate_limit_records_rate_limited_and_skips_send(channel):
    s, sender = channel(rate=2)
    for i in range(3):
        run(dedup=f"k{i}")
    assert len(sender.calls) == 2
    assert ledger(s) == ["sent", "sent", "rate_limited"]


def test_rate_limit_is_per_channel(channel, fake_redis):
    s, sender = channel(rate=1)
    run()
    tasks.send_notification.apply(args=(ORG, "ch-2", "k", None, payload()))
    assert len(sender.calls) == 2


def test_failure_is_ledgered_then_retried_until_success(channel):
    s, sender = channel(sender=FlakySender(fail_times=2))
    r = run()
    assert r.successful()
    assert len(sender.calls) == 3
    assert ledger(s) == ["failed", "failed", "sent"]


def test_failure_gives_up_after_max_retries(channel):
    s, sender = channel(sender=FlakySender(fail_times=99))
    r = run()
    assert r.failed()
    assert len(sender.calls) == tasks.send_notification.max_retries + 1
    assert set(ledger(s)) == {"failed"}


def test_ledger_error_is_a_category_not_the_raw_exception(channel):
    s, _ = channel(sender=FlakySender(fail_times=99, exc=RuntimeError("x" * 500)))
    run()
    errs = [p["e"] for _, p in s.statements("INSERT INTO notification_ledger") if p.get("st") == "failed"]
    assert errs and all(len(e) <= 200 for e in errs)


def test_oncall_success_tracks_open_alert_and_recovery_clears_it(channel, fake_redis):
    channel(kind="pagerduty")
    key = oncall.open_key(ORG, "j1")
    run()
    assert fake_redis.smembers(key) == {CH}
    assert fake_redis.ttl(key) > 0
    run(p=payload(condition="recovered", new_status="recovered"))
    assert fake_redis.smembers(key) == set()


def test_oncall_test_send_is_not_tracked(channel, fake_redis):
    channel(kind="pagerduty")
    run(p={"test": True, "test_id": "t1", "severity": "low", "condition": "failed", "new_status": "failed", "job_id": "j1"})
    assert fake_redis.smembers(oncall.open_key(ORG, "j1")) == set()


def test_chat_channel_success_is_not_tracked_as_oncall(channel, fake_redis):
    channel(kind="slack")
    run()
    assert fake_redis.smembers(oncall.open_key(ORG, "j1")) == set()


# ---- known bugs (strict xfail: flips red when fixed) ----------------------------------------

@pytest.mark.xfail(strict=True, reason="review #19: retries count against the channel's per-minute limit, so a "
                                       "flaky first attempt can push the next real alert into rate_limited (tasks.py:100-105)")
def test_retries_do_not_eat_the_rate_limit(channel):
    s, sender = channel(rate=2, sender=FlakySender(fail_times=1))
    run(dedup="alert-a")   # attempt fails, eager retry succeeds
    run(dedup="alert-b")   # a second, distinct alert in the same minute
    assert ledger(s)[-1] == "sent"


@pytest.mark.xfail(strict=True, reason="review #19: a destination netguard blocks is retried max_retries times "
                                       "even though it can never succeed (tasks.py:122-132)")
def test_blocked_destination_is_not_retried(channel):
    _, sender = channel(sender=FlakySender(fail_times=99, exc=netguard.BlockedDestination("blocked: private address")))
    run()
    assert len(sender.calls) == 1
