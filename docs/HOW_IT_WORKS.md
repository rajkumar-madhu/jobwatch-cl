# How JobWatch works

## Two entry points
- The **ingest host** receives machine traffic: pings, the heartbeat API, the Linux and Kubernetes
  agents, and Healthchecks- and Cronitor-compatible paths. It has a high per-address limit, because one
  server can run many jobs.
- The **app host** serves people: the web app, the API, sign-in, single sign-on and SCIM.

## From ping to alert
1. A job pings (or the agent reports a run). Ingest checks the token against a cache and the per-job
   rate limit, then puts the event on a **durable queue** (NATS JetStream) and answers.
2. The **execution processor** records the run, matches it to its expected slot, applies
   [run checks](run-checks), and updates the job's state.
3. When a job's state changes, the **rule engine** applies your alert rules, groups related failures
   into an incident, and the **notifier** sends the alert with full context ([Alerts](alerts)). When the
   job recovers, PagerDuty and Opsgenie alerts resolve themselves.

## From silence to alert
JobWatch does not wait for a ping to notice a problem:
1. The **schedule generator** creates each expected run ahead of time from the job's schedule.
2. The **reconciler** marks a run late, then missed, when its deadline passes with nothing matched,
   and the rule engine alerts as above.
3. If JobWatch itself was not watching at that moment, the run is marked **unobserved** instead, so an
   outage of ours never pages you.

## Storage
PostgreSQL holds everything, with row-level security per organization. Redis holds caches and rate
limits (never plaintext tokens). The queue is durable, so a worker restart or a slow downstream never
loses a ping; events wait and are replayed.

## Data leaving JobWatch
Only through destinations you configure: alert channels, signal destinations, audit-log streams and
metrics exports (for example [Datadog](datadog)).
