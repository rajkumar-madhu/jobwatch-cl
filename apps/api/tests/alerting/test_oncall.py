"""PagerDuty / Opsgenie senders (alerting/oncall.py), driven through httpx.MockTransport."""
import json

import httpx
import pytest

from cronsentinel.alerting import oncall

PD_KEY = "a" * 32
OG_KEY = "0123abcd-0000-4000-8000-0123456789ab"


def recorder(status=202):
    sent = []

    def handler(req: httpx.Request):
        sent.append(req)
        return httpx.Response(status, json={})

    return sent, httpx.Client(transport=httpx.MockTransport(handler))


def payload(**kw):
    p = {"job_id": "j1", "job_name": "backup", "prev_status": "healthy", "new_status": "failed",
         "condition": "failed", "severity": "high", "incident_id": "i1", "execution_id": "e1", "ts": "2026-10-12T00:00:00Z"}
    p.update(kw)
    return p


# ---- config validation ----------------------------------------------------------------------

def test_validate_accepts_good_keys_and_regions():
    assert oncall.validate("pagerduty", {"routing_key": PD_KEY}) is None
    assert oncall.validate("pagerduty", {"routing_key": PD_KEY, "region": "eu"}) is None
    assert oncall.validate("opsgenie", {"api_key": OG_KEY}) is None


@pytest.mark.parametrize("kind,cfg", [
    ("pagerduty", {"routing_key": "short"}),
    ("pagerduty", {"routing_key": PD_KEY + "x"}),
    ("pagerduty", {}),
    ("opsgenie", {"api_key": "not-a-uuid"}),
    ("pagerduty", {"routing_key": PD_KEY, "region": "ap"}),
])
def test_validate_rejects_bad_config(kind, cfg):
    assert oncall.validate(kind, cfg)


def test_alert_key_is_stable_per_job_and_isolated_for_tests():
    assert oncall.alert_key(payload()) == oncall.alert_key(payload(condition="recovered")) == "jobwatch-job-j1"
    assert oncall.alert_key({"test": True, "test_id": "t9"}) == "jobwatch-test-t9"


def test_open_key_namespaced_by_org_and_job():
    assert oncall.open_key("o1", "j1") != oncall.open_key("o2", "j1")


# ---- PagerDuty ------------------------------------------------------------------------------

def test_pagerduty_trigger_uses_dedup_key_and_mapped_severity():
    sent, c = recorder()
    oncall.send_pagerduty({"routing_key": PD_KEY}, "T", "B", payload(), client=c)
    assert len(sent) == 1
    req = sent[0]
    assert str(req.url) == "https://events.pagerduty.com/v2/enqueue"
    ev = json.loads(req.content)
    assert ev["event_action"] == "trigger"
    assert ev["dedup_key"] == "jobwatch-job-j1"
    assert ev["payload"]["severity"] == "error"  # high → error
    assert ev["payload"]["custom_details"]["status"] == "healthy → failed"


def test_pagerduty_recovery_resolves_the_same_key():
    sent, c = recorder()
    oncall.send_pagerduty({"routing_key": PD_KEY, "region": "eu"}, "T", "B", payload(condition="recovered"), client=c)
    ev = json.loads(sent[0].content)
    assert str(sent[0].url).startswith("https://events.eu.pagerduty.com")
    assert ev == {"routing_key": PD_KEY, "event_action": "resolve", "dedup_key": "jobwatch-job-j1"}


def test_pagerduty_test_send_triggers_then_resolves():
    sent, c = recorder()
    oncall.send_pagerduty({"routing_key": PD_KEY}, "T", "B", {"test": True, "test_id": "t1", "severity": "low"}, client=c)
    actions = [json.loads(r.content)["event_action"] for r in sent]
    assert actions == ["trigger", "resolve"]


def test_pagerduty_dashboard_link_and_long_summary_truncated():
    sent, c = recorder()
    oncall.send_pagerduty({"routing_key": PD_KEY, "dashboard_url": "https://app.example/"}, "x" * 2000, "B", payload(), client=c)
    ev = json.loads(sent[0].content)
    assert ev["links"][0]["href"] == "https://app.example/jobs/j1"
    assert len(ev["payload"]["summary"]) == 1024


def test_pagerduty_http_error_raises_so_the_task_can_retry():
    _, c = recorder(status=500)
    with pytest.raises(httpx.HTTPStatusError):
        oncall.send_pagerduty({"routing_key": PD_KEY}, "T", "B", payload(), client=c)


# ---- Opsgenie -------------------------------------------------------------------------------

def test_opsgenie_creates_alert_with_alias_and_priority():
    sent, c = recorder()
    oncall.send_opsgenie({"api_key": OG_KEY, "responders": [{"type": "team", "name": "ops"}]}, "T", "B",
                         payload(severity="critical"), client=c)
    assert len(sent) == 1
    req = sent[0]
    assert req.headers["Authorization"] == f"GenieKey {OG_KEY}"
    body = json.loads(req.content)
    assert body["alias"] == "jobwatch-job-j1" and body["priority"] == "P1"
    assert body["responders"] == [{"type": "team", "name": "ops"}]


def test_opsgenie_recovery_closes_by_alias():
    sent, c = recorder()
    oncall.send_opsgenie({"api_key": OG_KEY}, "T", "B", payload(condition="recovered"), client=c)
    assert len(sent) == 1
    assert sent[0].url.path == "/v2/alerts/jobwatch-job-j1/close"
    assert sent[0].url.params["identifierType"] == "alias"


def test_opsgenie_test_send_creates_then_closes():
    sent, c = recorder()
    oncall.send_opsgenie({"api_key": OG_KEY}, "T", "B", {"test": True, "test_id": "t1"}, client=c)
    assert [r.url.path for r in sent] == ["/v2/alerts", "/v2/alerts/jobwatch-test-t1/close"]


def test_opsgenie_http_error_raises():
    _, c = recorder(status=422)
    with pytest.raises(httpx.HTTPStatusError):
        oncall.send_opsgenie({"api_key": OG_KEY}, "T", "B", payload(), client=c)
