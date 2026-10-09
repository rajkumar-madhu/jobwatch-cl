"""R54: assertion grammar and evaluation."""
import pytest

from cronsentinel import assertions as A


@pytest.mark.parametrize("rule,parsed", [
    ("metric.size_bytes > 0", ("size_bytes", ">", 0.0)),
    ("metric.rows >= 1000", ("rows", ">=", 1000.0)),
    ("duration < 2h", ("duration", "<", 7200.0)),
    ("duration > 30s", ("duration", ">", 30.0)),
    ("metric.duration < 30min", ("duration", "<", 1800.0)),     # Cronitor's spelling
    ("metric.error_count = 0", ("error_count", "=", 0.0)),
    ("  METRIC.Count != 3 ", ("count", "!=", 3.0)),
])
def test_parse(rule, parsed):
    assert A.parse(rule) == parsed


@pytest.mark.parametrize("rule,why", [
    ("metric.p95 < 5 over 24 hours", "aggregate"), ("metric.duration.p95 < 5", "aggregate"),
    ("size > 0", "expected"), ("metric.x > big", "not a number"), ("duration < soon", "not a duration"),
    ("metric.x > inf", "finite"),
])
def test_rejects(rule, why):
    with pytest.raises(A.InvalidAssertion, match=why):
        A.parse(rule)


def test_evaluate():
    rules = ["metric.size_bytes > 0", "duration < 1h", "metric.rows >= 10"]
    assert A.evaluate(rules, {"size_bytes": 512, "rows": 10}, 60_000) == []
    broken = A.evaluate(rules, {"size_bytes": 0}, 4_000_000)
    assert broken == ["metric.size_bytes > 0 (was 0)", "duration < 1h (took 4000 s)", "metric.rows >= 10 (metric not reported)"]
    assert A.evaluate(["duration < 1h"], {}, None) == ["duration < 1h (run duration unknown)"]


def test_validate_limits_and_normalises():
    assert A.validate(["metric.a  >   1"]) == ["metric.a > 1"]
    with pytest.raises(A.InvalidAssertion):
        A.validate(["metric.a > 1"] * 21)


def test_clean_metrics():
    assert A.clean_metrics({"Size_Bytes": "12", "bad name": 1, "x": float("nan"), "ok": True, "n": 3}) == {"size_bytes": 12.0, "n": 3.0}
