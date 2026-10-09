"""A stand-in for Cronitor's Monitor API, written to its public docs (cronitor.io/docs/monitors-api).
Cronitor is closed source; this fake is what the R50 importer is tested against. Differences from the
real service would show up here first — update the fake when Cronitor's docs or behaviour change.

Behaviour copied from the docs: HTTP Basic auth with the API key as username and no password; scoped
keys (monitor:read); page / pageSize paging; Cronitor-Version 2020-10-01 returns `schedule` (string),
2025-11-28 returns `schedules` (array). The list envelope is not documented; ENVELOPE selects one.
"""
import base64

from fastapi import FastAPI, Header, HTTPException, Request

READ_KEY = "cr_read_0123456789abcdef"
NO_SCOPE_KEY = "cr_telemetry_only_000000"
ENVELOPE = {"value": "monitors"}   # "monitors" | "data" | "list"

MONITORS = [
    {"key": "nightly-backup", "name": "Nightly backup", "type": "job", "schedule": "0 2 * * *", "timezone": "Asia/Kolkata",
     "grace_seconds": 1800, "assertions": ["metric.duration < 2h", "metric.count > 0"], "tags": ["prod", "db"],
     "note": "pg_dump to S3", "paused": False},
    {"key": "queue-worker", "name": "Queue worker", "type": "heartbeat", "schedule": "every 5 minutes", "grace_seconds": 60},
    {"key": "daily-report", "name": "Daily report", "type": "job", "schedule": "at 09:30", "timezone": "UTC"},
    {"key": "odd-job", "name": "Odd job", "type": "job", "schedule": "every 90 minutes"},
    {"key": "api-uptime", "name": "API uptime", "type": "check", "request": {"url": "https://example.com"}},
    {"key": "two-runs", "name": "Two runs a day", "type": "job", "schedules": ["0 6 * * *", "0 18 * * *"]},
] + [{"key": f"filler-{i:03d}", "name": f"Filler {i:03d}", "type": "heartbeat", "schedule": "every 1 hour"} for i in range(95)]

app = FastAPI()
calls: list[dict] = []


def _key(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Basic "):
        raise HTTPException(401, "authentication required")
    user, _, _pw = base64.b64decode(authorization[6:]).decode().partition(":")
    return user


@app.get("/api/monitors")
def monitors(request: Request, page: int = 1, pageSize: int = 50, authorization: str | None = Header(None),  # noqa: N803
             cronitor_version: str | None = Header(None)):
    key = _key(authorization)
    calls.append({"page": page, "pageSize": pageSize, "version": cronitor_version})
    if key == NO_SCOPE_KEY:
        raise HTTPException(403, "API key lacks the monitor:read scope")
    if key != READ_KEY:
        raise HTTPException(401, "invalid API key")
    out = []
    for m in MONITORS:
        m = dict(m)
        if cronitor_version == "2025-11-28":
            if "schedule" in m:
                m["schedules"] = [m.pop("schedule")]
        elif "schedules" in m:
            m["schedule"] = m["schedules"][0]          # older version shows one schedule
            if cronitor_version == "2020-10-01":
                m.pop("schedules")
        out.append(m)
    chunk = out[(page - 1) * pageSize: page * pageSize]
    if ENVELOPE["value"] == "list":
        return chunk
    return {ENVELOPE["value"]: chunk, "page": page, "page_size": pageSize, "total_count": len(out)}
