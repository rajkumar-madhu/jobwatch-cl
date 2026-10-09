# Import from Healthchecks.io or Cronitor

**Jobs → Import** brings your checks over. Preview first, then import. Keys are used for the request
only and never stored. Re-running an import creates nothing twice; your plan's job limit applies.

## Healthchecks.io (or self-hosted Healthchecks)
Use a project **API key** (Project → Settings → API Access).
- With the **read-write** key, every job keeps its check UUID as its ping token. In your scripts, change
  only the host: `hc-ping.com/` → `<ingest>/hc/`. Paths such as `/start`, `/fail` and exit codes keep working.
- A **read-only** key works too, but every job gets a new ping URL (shown once).

| Healthchecks check | JobWatch job |
|---|---|
| cron schedule with time zone and grace | same schedule, time zone and grace |
| interval ("every hour") | a cron that fires every interval, flagged for review |
| interval with no cron equivalent (e.g. 90 minutes) | imported without a schedule, flagged |
| systemd `OnCalendar` schedule | translated to cron when exact, otherwise reported |
| paused, tags, description | carried over |

## Cronitor
Use an API key with the `monitor:read` scope, and optionally your **telemetry key** (the key inside
your `cronitor.link` URLs).
- With the telemetry key, jobs keep their Cronitor URLs: change `cronitor.link/` → `<ingest>/cronitor/`.
  `state=`, `series=` and `metric=` keep working. Without it, every job gets a new ping URL.
- `job` and `heartbeat` monitors are imported; `check` and `site` monitors (HTTP uptime) are listed but
  not imported.
- Cron schedules are kept; "every 5 minutes" and "at 09:30" are converted; extra schedules are reported.
- `metric.duration < X` becomes the job's expected runtime; other per-run assertions become
  [run checks](run-checks); aggregate assertions are reported.
