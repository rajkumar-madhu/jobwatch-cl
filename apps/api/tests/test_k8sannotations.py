"""R65: jobwatch.io/* annotations on CronJobs."""
from cronsentinel import k8sannotations as K


def test_parses_everything():
    s = K.parse({"jobwatch.io/name": "Billing report", "jobwatch.io/grace": "10m", "jobwatch.io/expected-runtime": "1h30m",
                 "jobwatch.io/tags": "Billing, prod", "jobwatch.io/run-checks": "duration < 2h;  metric.rows   > 0",
                 "jobwatch.io/description": "Nightly invoices", "other.io/x": "y"})
    assert (s.name, s.grace_s, s.expected_runtime_s) == ("Billing report", 600, 5400)
    assert s.tags == ["billing", "prod"] and s.run_checks == ["duration < 2h", "metric.rows > 0"]
    assert s.description == "Nightly invoices" and not s.ignore and s.warnings == []


def test_invalid_values_warn_and_are_not_applied():
    s = K.parse({"jobwatch.io/grace": "soon", "jobwatch.io/expected-runtime": "30d", "jobwatch.io/tags": "ok, Bad Tag!",
                 "jobwatch.io/run-checks": "metric.p95 < 5 over 24 hours", "jobwatch.io/ignore": "maybe", "jobwatch.io/colour": "red"})
    assert s.grace_s is None and s.expected_runtime_s is None and s.run_checks is None and not s.ignore
    assert s.tags == ["ok"]
    joined = " | ".join(s.warnings)
    for frag in ("not a duration", "7 days", "invalid tags ignored: bad tag!", "aggregate", "true or false", "unknown annotation"):
        assert frag in joined, frag


def test_ignore_and_durations():
    assert K.parse({"jobwatch.io/ignore": "true"}).ignore
    assert [K.duration_s(x) for x in ("90", "2h", "1d", "1h 30m", "", "abc")] == [90, 7200, 86400, 5400, None, None]
