"""R48 — import checks from Healthchecks.io (or a self-hosted Healthchecks) via its v3 Management API.

Mapping (verified against Healthchecks' own server, see tests):
  cron check        schedule + tz + grace            → same cron schedule, tz and grace
  simple check      timeout (period) + grace         → cron that fires every period, if one exists
                                                       (60 s … 1 day steps, 1 week); grace = period + grace,
                                                       because a simple check's runs keep their own phase
  OnCalendar check  systemd schedule                 → cron when exact (R53, cronsentinel/oncalendar.py), else reported
  paused            status == "paused"               → imported paused
  tags / desc                                        → tags / description

Ping URLs: with a read-write API key Healthchecks returns each check's UUID. The imported job keeps that
UUID as its ping token, and ingest answers Healthchecks-style paths under /hc/, so scripts only change
the host: https://hc-ping.com/<uuid>/fail → https://<ingest>/hc/<uuid>/fail. A read-only key does not
expose UUIDs; those jobs get new ping URLs, returned once in the import result.
"""
import re

from .. import netguard, oncalendar, schedule

# period (s) → cron that fires every period. Only periods that divide evenly into the clock map.
_PERIOD_CRON = {60: "* * * * *", 120: "*/2 * * * *", 300: "*/5 * * * *", 600: "*/10 * * * *", 900: "*/15 * * * *",
                1200: "*/20 * * * *", 1800: "*/30 * * * *", 3600: "0 * * * *", 7200: "0 */2 * * *",
                10800: "0 */3 * * *", 14400: "0 */4 * * *", 21600: "0 */6 * * *", 28800: "0 */8 * * *",
                43200: "0 */12 * * *", 86400: "0 0 * * *", 604800: "0 0 * * 0"}
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_CHECKS = 5000


class ImportError_(Exception):
    pass


def fetch(base_url: str, api_key: str) -> list[dict]:
    """GET <base>/api/v3/checks/ through the SSRF guard (base_url is tenant-supplied)."""
    base = (base_url or "https://healthchecks.io").rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise ImportError_("base_url must be an http(s) URL")
    url = f"{base}/api/v3/checks/"
    try:
        netguard.check_url(url)
        with netguard.client(timeout=20) as c:
            r = c.get(url, headers={"X-Api-Key": api_key})
    except netguard.BlockedDestination as e:
        raise ImportError_(f"base_url: {e}") from None
    except Exception:
        raise ImportError_("could not reach the Healthchecks API") from None
    if r.status_code == 401:
        raise ImportError_("Healthchecks rejected the API key")
    if r.status_code != 200:
        raise ImportError_(f"Healthchecks API returned {r.status_code}")
    try:
        checks = r.json()["checks"]
    except Exception:
        raise ImportError_("unexpected response from the Healthchecks API") from None
    if len(checks) > MAX_CHECKS:
        raise ImportError_(f"more than {MAX_CHECKS} checks; import per project")
    return checks


def map_check(c: dict) -> dict:
    """One Healthchecks check → {'job': {...} | None, 'token': uuid | None, 'status': ..., 'note': ...}."""
    name = (c.get("name") or c.get("slug") or "unnamed check")[:200]
    tags = [t for t in (c.get("tags") or "").split() if t][:20]
    grace = int(c.get("grace") or 3600)
    token = c.get("uuid") if UUID_RE.fullmatch(str(c.get("uuid") or "")) else None
    base = {"name": name, "description": (c.get("desc") or None), "tags": tags, "kind": "heartbeat",
            "paused": c.get("status") == "paused", "tz": "UTC"}
    sched, tz, timeout = c.get("schedule"), c.get("tz"), c.get("timeout")
    if sched:
        if schedule.validate(sched):
            return {"job": {**base, "schedule_expr": sched, "tz": tz or "UTC", "grace_s": grace}, "token": token,
                    "status": "imported", "note": None}
        # R53: OnCalendar checks are translated when cron can express them exactly
        try:
            cron, cal_tz = oncalendar.calendar_to_cron(sched)
        except oncalendar.Untranslatable as e:
            return {"job": None, "token": None, "status": "skipped",
                    "note": f"systemd schedule '{sched}' has no exact cron form ({e}); create this job by hand"}
        return {"job": {**base, "schedule_expr": cron, "tz": cal_tz or tz or "UTC", "grace_s": grace}, "token": token,
                "status": "converted", "note": f"systemd schedule '{sched}' became cron '{cron}'"}
    if timeout:
        cron = _PERIOD_CRON.get(int(timeout))
        if cron:
            return {"job": {**base, "schedule_expr": cron, "grace_s": int(timeout) + grace}, "token": token,
                    "status": "converted",
                    "note": f"every {int(timeout)} s became '{cron}' with grace {int(timeout) + grace} s; review the schedule"}
        return {"job": {**base, "schedule_expr": None, "grace_s": grace}, "token": token, "status": "needs_schedule",
                "note": f"a {int(timeout)} s period has no cron equivalent; imported without a schedule — set one to detect missed runs"}
    return {"job": None, "token": None, "status": "skipped", "note": "check has neither a schedule nor a period"}
