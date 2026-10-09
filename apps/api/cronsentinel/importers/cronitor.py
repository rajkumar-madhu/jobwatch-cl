"""R50 — import monitors from Cronitor via its Monitor API, keeping telemetry URLs working.

Built from Cronitor's public docs (Monitor API, Telemetry API); Cronitor is closed source, so the tests
use a fake server written to those docs (tests/fakes/cronitor.py). Validate against a real account
before relying on it.

  Auth           HTTP Basic, API key as username, empty password; needs scope monitor:read
  List           GET {base}/api/monitors?page=N&pageSize=100, header Cronitor-Version pinned to 2025-11-28
                 (`schedules` array; the older single `schedule` string is accepted too)
  Envelope       not shown in Cronitor's docs: a bare list, {"monitors": [...]} or {"data": [...]} are all
                 accepted; paging stops at the first short or empty page

Mapping:
  type job / heartbeat           imported (check / site are HTTP uptime and RUM: reported, not imported)
  cron schedule                  same expression, timezone, grace_seconds (Cronitor default 60 for jobs)
  "every N minutes|hours|days"   cron when the period divides the clock, as for Healthchecks intervals
  "at HH:MM"                     daily cron at that time
  several schedules              the first one is used; the rest are reported
  assertion metric.duration < X  expected runtime (timeout); other per-run assertions → job assertions (R54);
                                 aggregate ones (… over 24 hours) reported, not imported
  note / tags / paused           description / tags / paused

Ping URLs: Cronitor scripts call https://cronitor.link/p/<telemetry key>/<monitor key>?state=… . If the
import is given the telemetry key those scripts use, each job's ping token becomes "<key>/<monitor key>"
(stored hashed) and ingest serves the same path under /cronitor/, so only the host changes.
"""
import re

from .. import assertions, netguard, schedule

CRONITOR_VERSION = "2025-11-28"   # lists every schedule (`schedules`); 2020-10-01 shows only one
PAGE_SIZE = 100
MAX_MONITORS = 5000
_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
# period (s) → cron that fires every period; same table as the Healthchecks importer
_PERIOD_CRON = {60: "* * * * *", 120: "*/2 * * * *", 300: "*/5 * * * *", 600: "*/10 * * * *", 900: "*/15 * * * *",
                1200: "*/20 * * * *", 1800: "*/30 * * * *", 3600: "0 * * * *", 7200: "0 */2 * * *",
                10800: "0 */3 * * *", 14400: "0 */4 * * *", 21600: "0 */6 * * *", 28800: "0 */8 * * *",
                43200: "0 */12 * * *", 86400: "0 0 * * *", 604800: "0 0 * * 0"}
_DUR_UNITS = {"ms": 0.001, "s": 1, "sec": 1, "seconds": 1, "second": 1, "m": 60, "min": 60, "mins": 60, "minutes": 60,
              "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "hours": 3600}
KEY_RE = re.compile(r"[A-Za-z0-9_.\-]{1,100}")


class ImportError_(Exception):
    pass


def fetch(api_key: str, base_url: str = "https://cronitor.io") -> list[dict]:
    base = (base_url or "https://cronitor.io").rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise ImportError_("base_url must be an http(s) URL")
    monitors: list[dict] = []
    try:
        netguard.check_url(base + "/api/monitors")
        with netguard.client(timeout=20) as c:
            for page in range(1, MAX_MONITORS // PAGE_SIZE + 2):
                r = c.get(f"{base}/api/monitors", params={"page": page, "pageSize": PAGE_SIZE},
                          auth=(api_key, ""), headers={"Cronitor-Version": CRONITOR_VERSION, "Accept": "application/json"})
                if r.status_code in (401, 403):
                    raise ImportError_("Cronitor rejected the API key (it needs the monitor:read scope)")
                if r.status_code == 429:
                    raise ImportError_("Cronitor is rate limiting this key; try again in a minute")
                if r.status_code != 200:
                    raise ImportError_(f"Cronitor API returned {r.status_code}")
                body = r.json()
                batch = body if isinstance(body, list) else body.get("monitors") or body.get("data") or []
                monitors.extend(m for m in batch if isinstance(m, dict))
                if len(batch) < PAGE_SIZE or len(monitors) >= MAX_MONITORS:
                    break
    except netguard.BlockedDestination as e:
        raise ImportError_(f"base_url: {e}") from None
    except ImportError_:
        raise
    except Exception:
        raise ImportError_("could not reach the Cronitor API") from None
    return monitors[:MAX_MONITORS]


def translate_schedule(expr: str) -> tuple[str | None, int, str | None]:
    """Cronitor schedule string → (cron or None, extra grace seconds, note)."""
    e = (expr or "").strip()
    if schedule.validate(e):
        return e, 0, None
    m = re.fullmatch(r"every\s+(\d+)?\s*(second|minute|hour|day|week)s?", e, re.I)
    if m:
        period = int(m.group(1) or 1) * _UNITS[m.group(2).lower()]
        cron = _PERIOD_CRON.get(period)
        if cron:
            return cron, period, f"'{e}' became '{cron}' with extra grace {period} s; review the schedule"
        return None, 0, f"'{e}' has no cron equivalent; imported without a schedule — set one to detect missed runs"
    m = re.fullmatch(r"at\s+(\d{1,2}):(\d{2})", e, re.I)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(2))} {int(m.group(1))} * * *", 0, None
    return None, 0, f"schedule '{e}' could not be translated; imported without a schedule"


def _duration_seconds(text_: str) -> int | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*([a-z]*)\s*", text_, re.I)
    if not m:
        return None
    unit = _DUR_UNITS.get((m.group(2) or "s").lower())
    return int(float(m.group(1)) * unit) if unit else None


def map_monitor(mon: dict, telemetry_key: str | None) -> dict:
    """One Cronitor monitor → the shape routers/imports._apply expects."""
    key = str(mon.get("key") or "")
    name = str(mon.get("name") or mon.get("defaultName") or key or "unnamed monitor")[:200]
    mtype = mon.get("type")
    if mtype not in ("job", "heartbeat"):
        return {"job": None, "token": None, "status": "skipped",
                "note": f"Cronitor {mtype or 'unknown'} monitor (HTTP uptime / browser) — JobWatch monitors scheduled work"}
    notes: list[str] = []
    scheds = mon.get("schedules") or ([mon["schedule"]] if mon.get("schedule") else [])
    scheds = [s for s in scheds if isinstance(s, str) and s.strip()]
    cron, extra_grace, n = (translate_schedule(scheds[0]) if scheds else (None, 0, "no schedule in Cronitor"))
    if n:
        notes.append(n)
    if len(scheds) > 1:
        notes.append(f"Cronitor had {len(scheds)} schedules; only '{scheds[0]}' was imported")
    grace = int(mon.get("grace_seconds") if mon.get("grace_seconds") is not None else (60 if mtype == "job" else 0))
    runtime, rules = None, []
    for a in mon.get("assertions") or []:
        m = re.fullmatch(r"\s*metric\.duration\s*<\s*(.+)", str(a))
        sec = _duration_seconds(m.group(1)) if m else None
        if sec:
            runtime = sec                  # a timeout while the run is still going
            continue
        try:                               # R54: other per-run rules are enforced as job assertions
            assertions.parse(str(a))
            rules.append(" ".join(str(a).split()))
        except assertions.InvalidAssertion as e:
            notes.append(f"assertion not imported: {e}")
    tags = [str(t) for t in (mon.get("tags") or []) if str(t).strip()][:20]
    token = f"{telemetry_key}/{key}" if telemetry_key and KEY_RE.fullmatch(key) else None
    status = "imported" if cron and not any("became" in x for x in notes) else ("converted" if cron else "needs_schedule")
    if mon.get("environments"):
        notes.append("Cronitor environments are not separated in JobWatch; all pings go to this job")
    return {"job": {"name": name, "description": (mon.get("note") or None), "tags": tags, "kind": "heartbeat",
                    "paused": bool(mon.get("paused")), "tz": mon.get("timezone") or "UTC", "schedule_expr": cron,
                    "grace_s": grace + extra_grace, "expected_runtime_s": runtime, "assertions": rules[:assertions.MAX_ASSERTIONS]},
            "token": token, "status": status, "note": "; ".join(notes) or None}
