# Datadog (R58)

Three independent integrations, each needing only a Datadog **API key** (Organization Settings →
API Keys) and your **site** (US1 `datadoghq.com`, US3, US5, EU1 `datadoghq.eu`, AP1, AP2, UK1,
US1-FED `ddog-gov.com`, US2-FED). JobWatch only calls Datadog's own hosts for that site.

## 1. Alerts as Datadog events: Alerting → Add channel → Datadog
Every alert becomes an event in the Datadog event stream: the full alert subject as title, the alert
details as text, `alert_type` error / warning / success (recovered), and tags
`source:jobwatch`, `job:…`, `host:…`, `env:…`, `team:…`, `severity:…`, `condition:…`.
Events for one job share an aggregation key, so a failure and its recovery group together.
Plans: Team and above.

## 2. Audit log to Datadog Logs: Settings → Audit log streaming → Datadog Logs
Each audit event is sent to `https://http-intake.logs.<site>/api/v2/logs` with
`ddsource:jobwatch`, `service:jobwatch-audit`, an `action:` tag and the full event under `jobwatch`.
Same delivery as other audit streams: in order, at least once, catches up after outages.
Plans: Business and above; owners only.

## 3. Job metrics: Integrations → Datadog metrics
Sent every minute (window ends 30 s ago so runs have settled; catches up in 6-hour slices after an
outage), tagged `source:jobwatch`, `job`, `env`, `team`, `host`, plus your own tags:

| Metric | Type | Meaning |
|---|---|---|
| `jobwatch.run.duration` | gauge | seconds per finished run (`status:success` / `status:failed`) |
| `jobwatch.run.completed` | count | finished runs, by `status` |
| `jobwatch.run.missed` | count | expected runs that never started |
| `jobwatch.job.failing` | gauge | 1 while a job is failing, missed or timed out, else 0 |
| `jobwatch.job.reliability` | gauge | reliability score 0–100 |

Example monitor: `max(last_5m):max:jobwatch.job.failing{env:production} by {job} >= 1`.
Plans: Team and above; admins.

> [!TIP]
> After connecting, use **Check API key** (metrics) or **Send test** (alert channel) once with your real
> key. The integration was verified against a recording copy of Datadog's API, not a live account.
