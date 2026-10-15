"""Alert rules that the engine cannot evaluate are refused at create time
(code review 2026-10-10, finding #5)."""
from datetime import UTC, datetime

import pytest

from cronsentinel.alerting import rules as R

GOOD_BH = {"tz": "Asia/Kolkata", "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00"}


def test_valid_rule_has_no_errors():
    assert R.validate_rule("consecutive_failures", {"tags": ["db"]}, {"count": 3}, GOOD_BH) == []
    assert R.validate_rule("failed", {}, {}, None) == []
    assert R.validate_rule("failed", {"job_ids": None}, {"count": "5"}, None) == []   # numeric string from a form


@pytest.mark.parametrize("scope,params,bh,fragment", [
    ({}, {}, {"tz": "Mars/Olympus"}, "tz"),
    ({}, {}, {"tz": 5}, "tz"),
    ({}, {}, {"start": "9am"}, "start"),
    ({}, {}, {"end": "25:00"}, "end"),
    ({}, {}, {"days": [0, 8]}, "days"),
    ({}, {}, {"days": []}, "days"),
    ({}, {}, "weekdays", "business_hours"),
    ({}, {"count": "abc"}, None, "count"),
    ({}, {"count": 0}, None, "count"),
    ({}, {"count": True}, None, "count"),
    ({"tags": "db"}, {}, None, "scope.tags"),
    ({"job_ids": [1, 2]}, {}, None, "scope.job_ids"),
    ([], {}, None, "scope"),
    ({}, [], None, "params"),
])
def test_invalid_rule_is_reported(scope, params, bh, fragment):
    errs = R.validate_rule("consecutive_failures", scope, params, bh)
    assert errs and any(fragment in e for e in errs), errs


def test_unknown_condition():
    assert R.validate_rule("exploded", {}, {}, None)


def test_every_validated_business_hours_evaluates():
    """Whatever validate_rule accepts, in_business_hours must not raise on."""
    now = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    for bh in (GOOD_BH, {"tz": "UTC"}, {"start": "00:00", "end": "23:59"}, {"days": [6, 7]}, {}):
        assert R.validate_rule("failed", {}, {}, bh) == []
        R.in_business_hours(bh, now)
