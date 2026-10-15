"""Pure rule logic (alerting/rules.py): scope, conditions, business hours, flapping, dedup, repeat."""
from datetime import UTC, datetime, timedelta

import pytest

from cronsentinel.alerting import rules as R


def job(**kw):
    base = dict(id="j1", name="backup", tags=["db", "nightly"], environment_id="env-prod",
                workspace_id="ws1", team_id="team-a")
    base.update(kw)
    return R.JobCtx(**base)


def rule(condition="failed", **kw):
    return R.Rule(id="r1", condition=condition, **kw)


# ---- scope ----------------------------------------------------------------------------------

def test_empty_scope_matches_every_job():
    assert R.scope_matches(rule(scope={}), job())
    assert R.scope_matches(rule(scope=None), job())


@pytest.mark.parametrize("scope", [
    {"job_ids": ["j1"]},
    {"tags": ["nightly"]},
    {"environment_ids": ["env-prod"]},
    {"workspace_ids": ["ws1"]},
    {"team_ids": ["team-a"]},
])
def test_each_scope_key_matches(scope):
    assert R.scope_matches(rule(scope=scope), job())


def test_scope_keys_are_ored_not_anded():
    # job id doesn't match but tag does: still in scope
    assert R.scope_matches(rule(scope={"job_ids": ["other"], "tags": ["db"]}), job())


def test_scope_without_any_match_excludes_job():
    sc = {"job_ids": ["x"], "tags": ["web"], "environment_ids": ["env-dev"], "workspace_ids": ["ws2"], "team_ids": ["t2"]}
    assert not R.scope_matches(rule(scope=sc), job())


def test_scope_with_none_ids_on_job_does_not_match_none_in_list():
    assert not R.scope_matches(rule(scope={"team_ids": ["team-a"]}), job(team_id=None))


# ---- conditions -----------------------------------------------------------------------------

@pytest.mark.parametrize("cond,new,fires", [
    ("failed", "failed", True), ("failed", "timeout", False),
    ("missed", "missed", True), ("missed", "failed", False),
    ("late", "late", True),
    ("runtime_exceeded", "timeout", True), ("runtime_exceeded", "failed", False),
    ("recovered", "recovered", True), ("recovered", "healthy", False),
    ("nonsense", "failed", False),
])
def test_simple_conditions(cond, new, fires):
    assert R.condition_fires(rule(cond), "healthy", new, job()) is fires


def test_consecutive_failures_default_threshold_is_three():
    r = rule("consecutive_failures")
    assert not R.condition_fires(r, "failed", "failed", job(consecutive_failures=2))
    assert R.condition_fires(r, "failed", "failed", job(consecutive_failures=3))
    assert R.condition_fires(r, "failed", "timeout", job(consecutive_failures=3))
    assert not R.condition_fires(r, "failed", "missed", job(consecutive_failures=9))  # missed isn't counted here


def test_consecutive_failures_count_param_overrides_default():
    r = rule("consecutive_failures", params={"count": 1})
    assert R.condition_fires(r, "healthy", "failed", job(consecutive_failures=1))


def test_sla_breach_needs_both_target_and_rate():
    r = rule("sla_breach")
    assert R.condition_fires(r, "x", "failed", job(success_rate=97.0, sla_target=99.0))
    assert not R.condition_fires(r, "x", "failed", job(success_rate=99.5, sla_target=99.0))
    assert not R.condition_fires(r, "x", "failed", job(success_rate=None, sla_target=99.0))
    assert not R.condition_fires(r, "x", "failed", job(success_rate=50.0, sla_target=None))


def test_unknown_state_never_fires_any_condition():
    for c in R.CONDITIONS:
        assert not R.condition_fires(rule(c), "healthy", "failed", job(consecutive_failures=10, success_rate=0, sla_target=99),
                                     new_state="unknown", unknown_reason="agent_offline")


def test_suppressed_unknown_reason():
    assert R.suppressed_unknown("unknown", "agent_offline") == "unknown:agent_offline"
    assert R.suppressed_unknown("unknown", None) == "unknown:no_data"
    assert R.suppressed_unknown("healthy", "agent_offline") is None
    assert R.suppressed_unknown(None, None) is None


# ---- business hours -------------------------------------------------------------------------

BH = {"tz": "Asia/Kolkata", "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}


def test_no_business_hours_means_always():
    assert R.in_business_hours(None, datetime(2026, 10, 11, 3, 0, tzinfo=UTC))
    assert R.in_business_hours({}, datetime(2026, 10, 11, 3, 0, tzinfo=UTC))


def test_business_hours_use_the_rules_timezone():
    # Mon 2026-10-12 04:00 UTC == 09:30 IST → inside
    assert R.in_business_hours(BH, datetime(2026, 10, 12, 4, 0, tzinfo=UTC))
    # Mon 2026-10-12 03:00 UTC == 08:30 IST → before start
    assert not R.in_business_hours(BH, datetime(2026, 10, 12, 3, 0, tzinfo=UTC))
    # Mon 13:00 UTC == 18:30 IST → after end
    assert not R.in_business_hours(BH, datetime(2026, 10, 12, 13, 0, tzinfo=UTC))


def test_business_hours_weekday_is_evaluated_locally():
    # Fri 2026-10-16 20:00 UTC is Sat 01:30 IST → weekend locally, even though it's Friday in UTC
    assert not R.in_business_hours({**BH, "start": "00:00", "end": "23:59"}, datetime(2026, 10, 16, 20, 0, tzinfo=UTC))


def test_business_hours_bounds_are_inclusive():
    assert R.in_business_hours(BH, datetime(2026, 10, 12, 3, 30, tzinfo=UTC))   # 09:00 IST
    assert R.in_business_hours(BH, datetime(2026, 10, 12, 12, 30, tzinfo=UTC))  # 18:00 IST


def test_business_hours_defaults():
    # only tz given: Mon-Fri, whole day
    assert R.in_business_hours({"tz": "UTC"}, datetime(2026, 10, 12, 23, 30, tzinfo=UTC))
    assert not R.in_business_hours({"tz": "UTC"}, datetime(2026, 10, 11, 12, 0, tzinfo=UTC))  # Sunday


# ---- flapping / dedup / repeat --------------------------------------------------------------

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def test_flapping_needs_threshold_changes_inside_window():
    two = [NOW - timedelta(seconds=10), NOW - timedelta(seconds=20)]
    assert not R.is_flapping(two, NOW)
    assert R.is_flapping(two + [NOW - timedelta(seconds=599)], NOW)


def test_flapping_ignores_changes_outside_window():
    old = [NOW - timedelta(seconds=601)] * 5
    assert not R.is_flapping(old + [NOW], NOW)


def test_flapping_custom_window_and_threshold():
    ts = [NOW - timedelta(seconds=s) for s in (1, 2)]
    assert R.is_flapping(ts, NOW, window_s=5, threshold=2)
    assert not R.is_flapping(ts, NOW, window_s=1, threshold=2)


def test_dedup_key_is_rule_job_condition():
    assert R.dedup_key("r", "j", "failed") == "r:j:failed"
    assert R.dedup_key("r", "j", "failed") != R.dedup_key("r", "j", "flapping")


def test_should_repeat():
    assert R.should_repeat(None, None, NOW)  # never sent → send
    assert not R.should_repeat(NOW - timedelta(days=1), None, NOW)  # sent once, no repeat configured
    assert not R.should_repeat(NOW - timedelta(seconds=59), 60, NOW)
    assert R.should_repeat(NOW - timedelta(seconds=60), 60, NOW)
    assert not R.should_repeat(NOW - timedelta(hours=1), 0, NOW)  # 0 means "never repeat"
