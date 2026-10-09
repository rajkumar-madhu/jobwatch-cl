# What an alert tells you (R55)

Every alert names the job, where it ran, the environment, and why, so the person paged does not have
to open the dashboard to start working.

**Subject** (email subject, Slack/Teams/Discord title, PagerDuty summary, Opsgenie message):

```
✕ [HIGH] FAILED · nightly-db-backup on prod-db-01 (production) — exit 2 after 4m 12s
⚠ [CRITICAL] MISSED · invoice-run on billing-02 (production) — no run started for 2026-10-05 02:00 IST
✕ [HIGH] FAILED · nightly-db-backup on prod-db-01 (production) — run check failed: metric.size_bytes > 0 (was 0)
✓ [LOW] RECOVERED · report-sync in prod-eks/reports (production) — succeeded in 38s
```

**Details** (body; Slack fields; Teams facts; Discord embed; PagerDuty custom details; Opsgenie details;
the `context` object in signed webhooks), each shown only when known:

| Field | Source |
|---|---|
| Job, Status | the job and its state change |
| Server, or Cluster / namespace, Pod, Node | the run's host, the job's server, or its Kubernetes cluster |
| Environment, Workspace, Team, Tags | the job's settings |
| Schedule | e.g. `At 02:00 AM (0 2 * * *, Asia/Kolkata)` |
| Expected run | for missed and late alerts: the slot that did not start, in the job's time zone |
| Started, Finished, Duration, Exit code | the run that triggered the alert |
| Failed checks, Metrics | run checks the run broke and the numbers it reported |
| Last error | last line of the run's stderr, **redacted** (passwords, tokens, keys are masked) |
| Incident, Run, Open in JobWatch | ids and a direct link to the job |

Commands are deliberately left out: command lines often carry credentials in arguments. Set
`WEB_PUBLIC_URL` so the link points at your JobWatch.
