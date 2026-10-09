# terraform-provider-jobwatch

Manage JobWatch jobs and maintenance windows as code with the `wecrew/jobwatch` provider. It works
with Terraform and OpenTofu.

## Configure
```hcl
terraform {
  required_providers {
    jobwatch = { source = "wecrew/jobwatch", version = "~> 0.1" }
  }
}

provider "jobwatch" {
  api_url = "https://app.example.com"   # or JOBWATCH_API_URL
  # api_key from JOBWATCH_API_KEY: an API key with role devops or higher (Settings → API keys)
}
```

## Jobs
```hcl
resource "jobwatch_job" "backup" {
  name                     = "nightly-db-backup"
  schedule                 = "0 2 * * *"
  timezone                 = "Asia/Kolkata"
  grace_seconds            = 600
  expected_runtime_seconds = 3600
  tags                     = ["db", "prod"]
  run_checks               = ["metric.size_bytes > 0", "duration < 2h"]
}

output "backup_ping_url" {
  value     = jobwatch_job.backup.ping_url
  sensitive = true
}
```

| Attribute | Notes |
|---|---|
| `name` (required), `description` | |
| `schedule`, `timezone` | Cron expression and IANA zone (default `UTC`). Omit `schedule` for a job without a fixed schedule. |
| `grace_seconds` | Default 300. |
| `expected_runtime_seconds` | Runs still going after this long are flagged. |
| `tags`, `run_checks` | See [run checks](run-checks). Spacing differences in run checks are not treated as changes. |
| `paused` | Default `false`. |
| `workspace_id` | Defaults to the organization's first workspace; changing it replaces the job. |
| `ping_url` (computed, **sensitive**) | Pass it to the job's environment, a secret store or a CI variable. |
| `ping_token_hint` (computed) | The token's last characters, to recognise it without exposing it. |

> [!IMPORTANT]
> JobWatch shows a ping URL only once, at creation, so the provider keeps it in Terraform state.
> Protect your state accordingly (an encrypted remote backend). Jobs brought in with
> `terraform import jobwatch_job.x <job id>` have no `ping_url`: keep using the existing one, or
> choose **New ping URL** in the app.

## Maintenance windows
```hcl
resource "jobwatch_maintenance_window" "patching" {
  name      = "Sunday DB patching"
  starts_at = "2026-10-11T02:00:00+05:30"
  ends_at   = "2026-10-11T04:00:00+05:30"
  timezone  = "Asia/Kolkata"
  repeat    = "weekly"          # none, daily or weekly
  weekdays  = ["SU"]
  tags      = ["db"]            # or job_ids / environment_ids; all empty = every job
}
```
Windows cannot be edited in place: any change replaces the window. Times may use any offset; the same
instant written differently is not a change. See [maintenance windows](maintenance).

## Drift
`terraform plan` shows changes made in the app to managed attributes, and `apply` puts them back.
A job or window deleted in the app is recreated on the next `apply`.

## Development
```bash
go mod tidy && go vet ./... && go test ./... && go build -o terraform-provider-jobwatch .
```
`e2e/run.sh` runs the provider against a real JobWatch API with OpenTofu or Terraform (apply, a clean
plan, update, window replacement, drift detection and repair, import, destroy):
```bash
PROVIDER_BIN=./terraform-provider-jobwatch TF=tofu JOBWATCH_API_URL=http://localhost:8000 JOBWATCH_API_KEY=… e2e/run.sh
```
`go.sum` is not committed; CI generates it.

**Not yet done:** publishing to the Terraform and OpenTofu registries (a signed release with GPG and a
registry account). Until then, install from a filesystem mirror or with `dev_overrides`.
