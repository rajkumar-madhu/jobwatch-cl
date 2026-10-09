"""R58 — Datadog: alert events, audit logs and job metrics.

One client for the three integrations. Hosts come from Datadog's fixed site list (docs.datadoghq.com
"Getting Started with Datadog Sites"), never from tenant input, so these calls do not go through
netguard; the tenant supplies only an API key and a site.

  events   POST https://api.<site>/api/v1/events                         (alert channel)
  logs     POST https://http-intake.logs.<site>/api/v2/logs              (audit-log stream)
  metrics  POST https://api.<site>/api/v2/series                          (job metrics export)
  validate GET  https://api.<site>/api/v1/validate                        (settings "Test")
All authenticate with the DD-API-KEY header (an API key, not an application key).
"""
import re
from datetime import datetime

import httpx

SITES = {
    "datadoghq.com": "US1", "us3.datadoghq.com": "US3", "us5.datadoghq.com": "US5", "datadoghq.eu": "EU1",
    "ap1.datadoghq.com": "AP1 (Japan)", "ap2.datadoghq.com": "AP2 (Australia)", "uk1.datadoghq.com": "UK1",
    "ddog-gov.com": "US1-FED", "us2.ddog-gov.com": "US2-FED",
}
API_KEY_RE = re.compile(r"[0-9a-f]{32}")
SERIES_BATCH = 500
LOGS_BATCH = 1000          # Datadog's documented maximum entries per logs request


class DatadogError(Exception):
    pass


def _client(timeout: float) -> httpx.Client:
    """One place to build the HTTP client (tests replace it with a recording transport)."""
    return httpx.Client(timeout=timeout)


def validate(cfg: dict) -> str | None:
    """Config-time check; returns an error message or None."""
    if cfg.get("site", "datadoghq.com") not in SITES:
        return f"site must be one of: {', '.join(SITES)}"
    if not API_KEY_RE.fullmatch(str(cfg.get("api_key", ""))):
        return "api_key: expected a 32-character Datadog API key (Organization Settings → API Keys)"
    return None


def _site(cfg: dict) -> str:
    site = cfg.get("site") or "datadoghq.com"
    if site not in SITES:
        raise DatadogError("unknown Datadog site")
    return site


def _post(url: str, cfg: dict, json_body, client: httpx.Client | None, timeout: float = 15) -> httpx.Response:
    c = client or _client(timeout)
    r = c.post(url, json=json_body, headers={"DD-API-KEY": cfg["api_key"], "Content-Type": "application/json"})
    if r.status_code in (401, 403):
        raise DatadogError("Datadog rejected the API key")
    if r.status_code >= 300:
        raise DatadogError(f"Datadog answered HTTP {r.status_code}")
    return r


def check_key(cfg: dict, client: httpx.Client | None = None) -> None:
    c = client or _client(10)
    r = c.get(f"https://api.{_site(cfg)}/api/v1/validate", headers={"DD-API-KEY": cfg["api_key"]})
    if r.status_code != 200:
        raise DatadogError("Datadog rejected the API key" if r.status_code in (401, 403) else f"Datadog answered HTTP {r.status_code}")


def tag(k: str, v) -> str:
    """Datadog tags: lowercase, no spaces; key:value."""
    val = re.sub(r"[^a-z0-9_.:/\-]", "_", str(v).lower())[:180]
    return f"{k}:{val}"


# ---- events (alert channel) ----------------------------------------------------------------------

_ALERT_TYPE = {"failed": "error", "missed": "error", "timeout": "error", "consecutive_failures": "error",
               "late": "warning", "runtime_exceeded": "warning", "sla_breach": "warning", "flapping": "warning",
               "recovered": "success"}


def send_event(cfg: dict, title: str, body: str, p: dict, *, client: httpx.Client | None = None) -> None:
    c = p.get("context") or {}
    tags = ["source:jobwatch", tag("condition", p.get("condition", "")), tag("severity", p.get("severity", ""))]
    for k in ("job", "host", "environment", "team", "cluster", "namespace"):
        if c.get(k):
            tags.append(tag("env" if k == "environment" else k, c[k]))
    tags += [tag("jobwatch_tag", t) for t in (c.get("tags") or [])][:10]
    ev = {"title": title[:500], "text": f"%%%\n{body}\n%%%"[:4000], "alert_type": _ALERT_TYPE.get(p.get("condition"), "info"),
          "source_type_name": "jobwatch", "tags": tags,
          # one aggregation per job, so a failure and its recovery group together in Datadog
          "aggregation_key": f"jobwatch-job-{p.get('job_id') or 'test'}"[:100]}
    if c.get("host"):
        ev["host"] = c["host"]
    _post(f"https://api.{_site(cfg)}/api/v1/events", cfg, ev, client)


# ---- logs (audit stream) --------------------------------------------------------------------------

def send_logs(cfg: dict, events: list[dict], *, client: httpx.Client | None = None) -> None:
    items = [{"ddsource": "jobwatch", "service": cfg.get("service") or "jobwatch-audit", "hostname": "jobwatch",
              "ddtags": ",".join(["source:jobwatch", tag("action", e["action"])] + ([cfg["tags"]] if cfg.get("tags") else [])),
              "message": f"{e['action']} by {e['actor']['type']} {e['actor']['id'] or ''}".strip(),
              "date": e["time"], "jobwatch": e} for e in events]
    for i in range(0, len(items), LOGS_BATCH):
        _post(f"https://http-intake.logs.{_site(cfg)}/api/v2/logs", cfg, items[i:i + LOGS_BATCH], client)


# ---- metrics (job metrics export) -----------------------------------------------------------------

GAUGE, COUNT = 3, 1   # v2 series metric types


def point(metric: str, value: float, ts: datetime, tags: list[str], kind: int = GAUGE) -> dict:
    return {"metric": metric, "type": kind, "points": [{"timestamp": int(ts.timestamp()), "value": float(value)}], "tags": tags}


def send_series(cfg: dict, series: list[dict], *, client: httpx.Client | None = None) -> None:
    for i in range(0, len(series), SERIES_BATCH):
        _post(f"https://api.{_site(cfg)}/api/v2/series", cfg, {"series": series[i:i + SERIES_BATCH]}, client)
