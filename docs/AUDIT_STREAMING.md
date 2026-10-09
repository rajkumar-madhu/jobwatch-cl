# Streaming the audit log to a SIEM (R57)

Business and Enterprise owners can send every audit-log event to a SIEM as it happens:
**Settings → Audit log streaming → Add destination**.

## Delivery
- **In order, at least once.** Each destination keeps its own position in the audit log. If the SIEM
  is down, events wait in JobWatch and are delivered when it is back (retry backoff from 10 s up to
  15 minutes); nothing is dropped. Use the event `id` to drop the occasional duplicate.
- Up to 500 events per request, checked every 5 seconds by the outbound-exporter worker (no extra
  service to run). Several exporter replicas never send the same batch twice.
- Start from **new events only** or **include the last 30 days**.
- HTTPS only; destinations on private or internal addresses are refused.
- The settings page shows delivered and waiting counts, last success, and the last error.

## Event
```json
{"id": 18342, "time": "2026-10-05T11:58:02.114Z", "org_id": "…", "action": "scim.role_sync",
 "actor": {"type": "api_key", "id": "…"}, "target": {"type": "user", "id": "…"},
 "ip": "203.0.113.7", "details": {"from": "viewer", "to": "sre"}, "source": "jobwatch"}
```

## Signed webhook (Logstash, Vector, Fluent Bit, Sumo Logic, Elastic, …)
`POST <url>` with `{"source": "jobwatch", "type": "audit", "events": [ … ]}`. Verify
`X-JobWatch-Signature: sha256=<hex HMAC-SHA256 of the raw body with your secret>` and reject anything
that does not match. `X-JobWatch-Delivery` holds the first and last event id of the batch.

## Splunk HTTP Event Collector
URL `https://<splunk>:8088/services/collector/event`, your HEC token, and optionally an index.
Events arrive with `sourcetype=jobwatch:audit`, `source=jobwatch` and the event time.
