"""R42 — on-call integrations: PagerDuty (Events API v2) and Opsgenie (Alert API v2).

Both are stateful, unlike chat channels: an alert stays open on the on-call tool until it is
resolved. So every alert for a job uses the same stable key (`dedup_key` / `alias`) and a
`recovered` notification resolves/closes it, instead of paging someone a second time.

Destinations are fixed vendor hosts (US or EU region) chosen by us, never a tenant URL, so these
calls do not go through netguard; the tenant only supplies an opaque routing key / API key.
"""
import re

import httpx

PD_HOSTS = {"us": "https://events.pagerduty.com", "eu": "https://events.eu.pagerduty.com"}
OG_HOSTS = {"us": "https://api.opsgenie.com", "eu": "https://api.eu.opsgenie.com"}

# JobWatch rule severity → vendor severity / priority
PD_SEVERITY = {"critical": "critical", "high": "error", "medium": "warning", "low": "info"}
OG_PRIORITY = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}

PD_ROUTING_KEY = re.compile(r"[A-Za-z0-9]{32}")
OG_API_KEY = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def validate(kind: str, cfg: dict) -> str | None:
    """Config-time check. Returns an error message, or None when the config is usable."""
    region = (cfg.get("region") or "us")
    if region not in ("us", "eu"):
        return "region must be 'us' or 'eu'"
    if kind == "pagerduty" and not PD_ROUTING_KEY.fullmatch(str(cfg.get("routing_key", ""))):
        return "routing_key: expected the 32-character integration key from a PagerDuty Events API v2 integration"
    if kind == "opsgenie" and not OG_API_KEY.fullmatch(str(cfg.get("api_key", ""))):
        return "api_key: expected an Opsgenie API integration key (a UUID)"
    return None


def alert_key(p: dict) -> str:
    """One open alert per job. A test notification gets its own key so it never touches a real alert."""
    if p.get("test"):
        return f"jobwatch-test-{p.get('test_id', 'x')}"
    return f"jobwatch-job-{p['job_id']}"


def _resolving(p: dict) -> bool:
    return p.get("condition") == "recovered"


def send_pagerduty(cfg: dict, title: str, body: str, p: dict, *, client: httpx.Client | None = None) -> None:
    url = PD_HOSTS[(cfg.get("region") or "us")] + "/v2/enqueue"
    c = client or httpx.Client(timeout=10)
    key = alert_key(p)
    if _resolving(p):
        events = [{"routing_key": cfg["routing_key"], "event_action": "resolve", "dedup_key": key}]
    else:
        trigger = {
            "routing_key": cfg["routing_key"], "event_action": "trigger", "dedup_key": key,
            "payload": {
                "summary": title[:1024], "source": "JobWatch", "severity": PD_SEVERITY.get(p.get("severity", "medium"), "warning"),
                "component": p.get("job_name"), "class": p.get("condition"), "timestamp": p.get("ts"),
                "custom_details": {"job": p.get("job_name"), "status": f"{p.get('prev_status')} → {p.get('new_status')}",
                                   "incident_id": p.get("incident_id"), "execution_id": p.get("execution_id"), "details": body,
                                   **{k: v for k, v in (p.get("context") or {}).items() if isinstance(v, (str, int, float, list))}},
            },
        }
        if (p.get("context") or {}).get("link"):
            trigger["links"] = [{"href": p["context"]["link"], "text": "Open in JobWatch"}]
        elif cfg.get("dashboard_url") and p.get("job_id") and not p.get("test"):
            trigger["links"] = [{"href": f"{cfg['dashboard_url'].rstrip('/')}/jobs/{p['job_id']}", "text": "Open in JobWatch"}]
        events = [trigger]
        if p.get("test"):   # prove the key works, then close it again so nobody is left paged
            events.append({"routing_key": cfg["routing_key"], "event_action": "resolve", "dedup_key": key})
    for ev in events:
        c.post(url, json=ev).raise_for_status()


def send_opsgenie(cfg: dict, title: str, body: str, p: dict, *, client: httpx.Client | None = None) -> None:
    base = OG_HOSTS[(cfg.get("region") or "us")] + "/v2/alerts"
    c = client or httpx.Client(timeout=10)
    headers = {"Authorization": f"GenieKey {cfg['api_key']}"}
    key = alert_key(p)
    close = lambda: c.post(f"{base}/{key}/close", params={"identifierType": "alias"},  # noqa: E731
                           json={"source": "JobWatch", "note": "Recovered"}, headers=headers).raise_for_status()
    if _resolving(p):
        close()
        return
    alert = {"message": title[:130], "alias": key, "description": body[:15000], "source": "JobWatch",
             "priority": OG_PRIORITY.get(p.get("severity", "medium"), "P3"),
             "tags": [t for t in ("jobwatch", p.get("condition")) if t],
             "details": {k: str(v)[:500] for k, v in {"job": p.get("job_name"), "status": p.get("new_status"),
                         "incident_id": p.get("incident_id"), "execution_id": p.get("execution_id"),
                         **{ck: (", ".join(map(str, cv)) if isinstance(cv, list) else cv)
                            for ck, cv in (p.get("context") or {}).items() if not isinstance(cv, dict)}}.items() if v}}
    if cfg.get("responders"):
        alert["responders"] = cfg["responders"]
    c.post(base, json=alert, headers=headers).raise_for_status()
    if p.get("test"):
        close()


KINDS = ("pagerduty", "opsgenie")


def open_key(org_id: str, job_id: str) -> str:
    """Redis set of on-call channel ids holding an open alert for this job (filled by the notifier)."""
    return f"oncall-open:{org_id}:{job_id}"
