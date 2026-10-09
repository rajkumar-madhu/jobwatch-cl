terraform {
  required_providers {
    jobwatch = { source = "wecrew/jobwatch", version = "0.1.0" }
  }
}

provider "jobwatch" {}

resource "jobwatch_job" "backup" {
  name                     = "nightly-db-backup"
  description              = "pg_dump of the main database"
  schedule                 = "0 2 * * *"
  timezone                 = "Asia/Kolkata"
  grace_seconds            = 600
  expected_runtime_seconds = 3600
  tags                     = ["db", "prod"]
  run_checks               = ["metric.size_bytes  >   0", "duration < 2h"]
}

resource "jobwatch_maintenance_window" "patching" {
  name      = "Sunday DB patching"
  starts_at = "2026-10-11T02:00:00+05:30"
  ends_at   = "2026-10-11T04:00:00+05:30"
  timezone  = "Asia/Kolkata"
  repeat    = "weekly"
  weekdays  = ["su"]
  tags      = ["db"]
}

output "ping_url" {
  value     = jobwatch_job.backup.ping_url
  sensitive = true
}
