# Maintenance windows

Planned work should not page anyone. During a maintenance window, runs that come due are **skipped**:
they are not marked missed, they raise no alerts, and they do not count against a job's reliability.

## Schedule a window
**Alerting → Schedule maintenance**:

| Field | Meaning |
|---|---|
| Name | Shown in JobWatch and on public status pages, e.g. "Database patching" |
| Starts, lasts | When it begins and how long it lasts (up to 14 days; daily windows up to 24 hours) |
| Repeats | Once, every day, or every week on chosen days |
| Time zone | Repeating windows keep their local time across daylight-saving changes |
| Jobs | All jobs, or only jobs with given tags (the API also accepts job and environment ids) |

> [!NOTE]
> A repeating window follows its time zone's clock. "Sunday 03:00 Europe/London" is 02:00 UTC in
> summer and 03:00 UTC in winter. On the night the clocks go back, a time that happens twice uses
> the first one.

## While a window is active
- Runs due inside it are skipped once their grace period passes, unless they report anyway.
- State changes of covered jobs do not alert.
- Recoveries still close PagerDuty and Opsgenie alerts, so nobody stays paged.
- **End now** finishes a one-off window early, for when the work is done. Delete a repeating window to
  stop it.

## Status pages
Public status pages list the next windows that cover the jobs they show, with their names. Windows
for other jobs never appear on them.

## API
`POST /api/v1/alerts/maintenance` with `name`, `starts_at`, `ends_at`, `tz`, `repeat`
(`none` / `daily` / `weekly`), `weekdays` (e.g. `["SU"]`), optional `until`, and `scope`
(`tags`, `job_ids`, `environment_ids`). Listing shows whether each window is active now and its next
occurrence. Every change is in the audit log.
