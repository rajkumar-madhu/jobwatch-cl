# Linux agent

A single static binary (Linux amd64 and arm64) that runs as a systemd service. It finds your scheduled
jobs, reports every run, and keeps events on disk if the network or JobWatch is unavailable.

## Install
In **Servers & agents**, choose **Install an agent** (or **Get install command**). JobWatch creates a
one-time bootstrap token (valid 24 hours) and shows the command. Run it on the server as root:
```bash
curl -fsSL https://ingest.example.com/agent/install.sh | sudo bash -s -- --token csb_…
```
The installer is served by your JobWatch ingest host with its defaults filled in: which release to
download and which server to enroll with. It downloads the agent for your architecture, **verifies it
against the release's SHA256SUMS before installing anything**, enrolls the host and starts the systemd
service. The host receives its own agent key; the bootstrap token cannot be reused.

> [!NOTE]
> If the page says the one-line installer is not available, the deployment has not set
> `AGENT_DOWNLOAD_BASE` and `AGENT_VERSION` (the release pipeline sets both). You can still install from
> the release: download `install.sh` from it and run
> `sudo CS_DOWNLOAD_BASE=https://github.com/<owner>/<repo>/releases/download CS_AGENT_VERSION=v0.2.0 bash install.sh --server https://ingest.example.com --token csb_…`

## Discovery
Every few minutes the agent reads `/etc/crontab`, `/etc/cron.d/*`, user crontabs and systemd timers,
and creates a job for each (tagged `discovered`), with its schedule, command and user. Discovery never
edits your crontabs.

> [!NOTE]
> Timer schedules need agent version R53 or later. Older agents cannot report a timer's random delay,
> so their timers stay unscheduled rather than raise false alerts. Upgrade the agent to get them.

**systemd timers** get an expected schedule when cron can express it exactly: `daily`, `weekly`,
`Mon..Fri 09:00`, `*:0/15`, `*-*-01 03:00 UTC` and similar, and interval timers such as
`OnUnitActiveSec=15min`. Random delays (`RandomizedDelaySec`, e.g. certbot's 12 hours) widen the grace
period so delayed runs are not flagged. Schedules cron cannot express (a specific year, seconds, days
counted from month end, weekday and day-of-month together) are left unscheduled for you to set.

## Wrap runs with `cs-run`
Prefix a crontab command with `cs-run --` to report start, finish, exit code, duration and the end of
stdout and stderr:
```
0 2 * * * cs-run -- /opt/jobs/backup.sh
```
The wrapper exits with the job's own exit code, so cron mail and other behaviour are unchanged. Jobs
that do not run as root report through a private spool the agent collects.

To report metrics for [run checks](run-checks), append `name=value` lines to `$JOBWATCH_METRICS_FILE`:
```bash
echo "size_bytes=$(stat -c %s /backups/db.dump)" >> "$JOBWATCH_METRICS_FILE"
```

## When the network is down
Events are written to a disk buffer and sent in order when the connection returns; the server drops
duplicates. Corrupt lines are skipped rather than blocking the buffer.

## Security
- TLS is always verified; the server URL must be HTTPS.
- Only environment variable **names** from an allow-list are reported, never values.
- The systemd unit runs with `ProtectSystem=strict`, `NoNewPrivileges`, and memory and CPU limits.
- Agent keys are stored hashed server-side and can be rotated or revoked from Servers & agents.
