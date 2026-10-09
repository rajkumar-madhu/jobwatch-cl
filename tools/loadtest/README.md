# Ingest load test (R47)

`run.sh` seeds a fresh org with `JOBS` heartbeat jobs (default 2000), starts the full stack
(`apps/api/scripts/fullstack.sh`), drives `GET /ping/<token>` with the stdlib-only Go generator in
`hammer.go` at each concurrency in `CONC` for `DUR`, then waits for processing to drain and checks
that **every accepted ping became exactly one execution**.

```bash
JOBS=2000 CONC="20 100" DUR=15s bash tools/loadtest/run.sh
```

Reference numbers (R47, one shared vCPU running generator + ingest + workers + NATS + Postgres +
Redis, single ingest process, warm cache): about 320 pings/s at 20 clients (p50 55 ms), about 380
pings/s at 100 clients, zero loss and zero duplicates. Real nodes with dedicated cores do better;
scale ingest by replicas (Helm `replicas.ingest`, default 2).
