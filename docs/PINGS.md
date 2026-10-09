# Pings and heartbeat API

Every job has a secret **ping token**. It appears in the job's ping URL, is shown once when the job
is created (or when you choose **New ping URL**), and is stored only as a hash. All ping endpoints are
on the **ingest** host, not the app host.

> [!WARNING]
> A ping URL works like a password: anyone who has it can report runs for that job. If one leaks,
> choose **New ping URL** on the job; the old one stops working immediately.

## Simple pings
```bash
curl -fsS https://ingest.example.com/ping/<token>                  # success
curl -fsS https://ingest.example.com/heartbeat/<token>/start       # the run started
curl -fsS https://ingest.example.com/heartbeat/<token>/success     # the run succeeded
curl -fsS https://ingest.example.com/heartbeat/<token>/fail        # the run failed
```
Sending `start` lets JobWatch measure duration and flag runs that start but never finish. A `success`
or `fail` without a run id pairs with the job's most recent open `start`.

## JSON heartbeat API
`POST https://ingest.example.com/api/v1/heartbeat/<token>` with a JSON body:

| Field | Meaning | Limit |
|---|---|---|
| `status` | `start`, `success` or `fail` (required) | |
| `exit_code` | the command's exit code | −1024 to 1024 |
| `duration_ms` | how long it ran | up to 31 days |
| `execution_id` | your run id, so start and finish pair reliably | 128 characters |
| `sequence` | ordering when you send several events for one run | |
| `stdout_tail`, `stderr_tail` | the end of the output, shown with the run (secrets are redacted) | 256 KB each |
| `metrics` | numbers for [run checks](run-checks), e.g. `{"size_bytes": 7340032}` | 20 metrics |
| `meta` | any extra key-values | 16 KB |
| `host` | which machine ran it | 255 characters |
| `agent_ts` | when it happened, if the ping is delayed | clamped to a sane window |

```bash
curl -fsS -X POST https://ingest.example.com/api/v1/heartbeat/<token> \
  -H 'Content-Type: application/json' \
  -d '{"status":"fail","exit_code":2,"duration_ms":48210,"stderr_tail":"pg_dump: connection refused"}'
```

## Healthchecks.io-compatible paths
Jobs imported from Healthchecks.io keep their check UUID, under `/hc/`: plain, `/start`, `/fail`,
`/<exit status>`, `/log`, `?rid=` run ids, GET/POST/HEAD. Change only the host:
`https://hc-ping.com/<uuid>/fail` → `https://ingest.example.com/hc/<uuid>/fail`.

## Cronitor-compatible paths
Jobs imported from Cronitor with your telemetry key keep `/p/<key>/<monitor>` under `/cronitor/`,
with `state=run|complete|fail|tick|ok`, `series`, `metric=name:value`, `message`, `status_code`,
`host`, `env`. Change only the host: `https://cronitor.link/p/…` → `https://ingest.example.com/cronitor/p/…`.

## Limits and errors
- Each job accepts up to 600 pings a minute; beyond that the API answers `429` with `Retry-After`.
- An unknown token answers `404`. Repeatedly guessing tokens from one address is rate limited.
- Pings are acknowledged once they are durably queued, so a JobWatch worker restart never loses one.
