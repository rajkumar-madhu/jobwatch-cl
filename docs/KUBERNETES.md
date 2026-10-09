# Kubernetes

The in-cluster agent watches CronJobs and the Jobs they create, and reports discovery, starts,
successes and failures (with Kubernetes' reason, e.g. `BackoffLimitExceeded`). Nothing has to change in
your CronJob manifests.

## Install with Helm
Get a one-time bootstrap token from **Servers & agents → Get install command** (the `csb_…` value in the
command; valid 24 hours), then:
```bash
helm upgrade --install jobwatch-agent infra/helm/cronsentinel-agent -n jobwatch --create-namespace \
  --set server=https://ingest.example.com \
  --set clusterName=production \
  --set bootstrapToken=csb_…
```

| Value | Default | Meaning |
|---|---|---|
| `server` | | your JobWatch ingest URL (HTTPS) |
| `clusterName` | `production` | how the cluster is named in JobWatch and in alerts |
| `scope` | `cluster` | `cluster`, or `namespaces` to watch only the list below |
| `namespaces` | `[]` | namespaces to watch when `scope: namespaces` |
| `persistence.enabled` | `true` | a small volume that keeps the agent's identity and its event buffer |
| `existingSecret` | | a Secret with `CS_AGENT_ID` and `CS_AGENT_KEY`, instead of a bootstrap token |

## What it reports
- **Discovery:** every CronJob in scope, with its schedule and concurrency policy.
- **Runs:** start, then success or failure, once per Job (updates and resyncs do not repeat them).
- **Alerts** name the cluster and namespace: `… in production/reports (production)`.

## Configure monitoring on the CronJob
Add `jobwatch.io/*` annotations to a CronJob; the agent applies them on its next discovery:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: invoice-export
  namespace: billing
  annotations:
    jobwatch.io/name: "Invoice export"
    jobwatch.io/grace: "10m"
    jobwatch.io/expected-runtime: "2h"
    jobwatch.io/tags: "billing, prod"
    jobwatch.io/run-checks: "duration < 2h"
    jobwatch.io/description: "Nightly invoice export to the ledger"
spec:
  schedule: "0 2 * * *"
  timeZone: "Asia/Kolkata"
```

| Annotation | Effect |
|---|---|
| `jobwatch.io/name` | Display name (default `namespace/name`) |
| `jobwatch.io/grace` | How late a run may start, e.g. `90`, `10m`, `1h30m` (up to 7 days) |
| `jobwatch.io/expected-runtime` | Runs still going after this long are flagged |
| `jobwatch.io/tags` | Comma-separated; added to `kubernetes` and the namespace |
| `jobwatch.io/run-checks` | [Run checks](run-checks) separated by `;` |
| `jobwatch.io/description` | Free text |
| `jobwatch.io/ignore` | `"true"`: do not monitor this CronJob |

- **A present annotation is the source of truth.** Change it in the app and the next discovery puts the
  annotated value back. An absent annotation leaves edits made in the app alone.
- **Invalid values are not applied.** The Kubernetes page shows the problem next to the CronJob.
- **`ignore`** keeps a new CronJob out of JobWatch (listed as *not monitored*). Added to a monitored one,
  it pauses the job and keeps its history; remove it and monitoring resumes.
- **`spec.timeZone`** is honoured, so a `0 2 * * *` schedule in `Asia/Kolkata` is expected at 02:00 Kolkata
  time. When the schedule or time zone changes, runs planned for the old one are dropped.
- Only `jobwatch.io/*` annotations leave the cluster. Others, such as `last-applied-configuration`, can
  contain secrets and are never sent.

> [!NOTE]
> Annotations and time zones need Kubernetes agent R65 or later.

## When JobWatch is unreachable
Events are kept in a bounded on-disk queue (on the persistent volume) and sent when the connection
returns.
