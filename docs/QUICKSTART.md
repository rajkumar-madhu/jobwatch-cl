# Quick start

JobWatch expects every scheduled run before it is due, so a job that fails, runs late or never starts
is caught on time. This page gets a first job monitored in a few minutes.

## 1. Create an account
Sign up with your work email (or Google, GitHub or Microsoft if your installation offers them). If your
company already uses JobWatch with single sign-on, you join its workspace automatically.

## 2. Monitor your first job with a ping URL
1. **Jobs → Add job.** Give it a name and its cron schedule (for example `0 2 * * *`) and time zone.
2. Copy the ping URL. It is shown once; JobWatch stores only a hash of it.
3. Call it at the end of the job:
   ```bash
   0 2 * * * /opt/jobs/backup.sh && curl -fsS -m 10 --retry 3 https://ingest.example.com/ping/<token>
   ```
If 02:00 passes without a ping (plus the grace period), the run is marked **missed** and you are alerted.
To report start, failure and exit codes too, see [Pings and heartbeat API](pings).

## 3. Or let the agent find your jobs
On Linux servers, the agent discovers crontabs and systemd timers, wraps runs with `cs-run`, and reports
start, finish, exit code, duration and output automatically. See [Linux agent](linux-agent). For
Kubernetes CronJobs, install the in-cluster agent with Helm: [Kubernetes](kubernetes).

## 4. Decide who hears about it
**Alerting → Add channel** (Slack, Microsoft Teams, Discord, Telegram, email, webhooks, PagerDuty,
Opsgenie, Datadog), then **Add rule** (for example "Job failed or missed, tag prod → #ops-alerts").
What an alert contains is described in [Alerts](alerts).

## 5. Catch runs that "succeed" but do the wrong thing
Add [run checks](run-checks) such as `metric.size_bytes > 0` so an empty backup counts as a failure.

## Moving from another tool?
Import from Healthchecks.io or Cronitor and keep your existing ping URLs: [Import](import).
