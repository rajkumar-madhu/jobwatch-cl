# Run checks (R54)

A run that exits 0 can still have done the wrong thing: the backup that wrote an empty file, the sync
that processed nothing. **Run checks** are rules a successful run must also satisfy. If any rule is
broken, the run is recorded as **failed**, with the reasons, and the normal alerts and incidents fire.

## Writing checks
On a job's page, under **Run checks**, one rule per line:

```
metric.size_bytes > 0
metric.rows >= 1000
duration < 2h
duration > 30s
```

- `metric.<name> <op> <number>`: `<op>` is `<`, `<=`, `>`, `>=`, `=` or `!=`.
- `duration <op> <time>`: time is a number with `ms`, `s`, `m`, `h` or `d`.
- A metric named in a rule but **not reported** by the run fails that rule, so a script that silently
  stops reporting its size is caught too.
- Up to 20 rules per job. Aggregates over time (Cronitor's `… over 24 hours`) are not supported.

## Reporting metrics from a run
Metric names are lowercase letters, digits and `_`; up to 20 per run; numbers only.

**With the Linux agent (`cs-run`)**, append `name=value` lines to the file named in
`$JOBWATCH_METRICS_FILE` (private to the job's user, removed afterwards):

```bash
pg_dump mydb > /backups/db.dump
echo "size_bytes=$(stat -c %s /backups/db.dump)" >> "$JOBWATCH_METRICS_FILE"
```

**With the heartbeat API**, add `metrics` to the JSON body:

```bash
curl -fsS -X POST "$INGEST/api/v1/heartbeat/$TOKEN" -H 'Content-Type: application/json' \
  -d '{"status":"success","exit_code":0,"metrics":{"size_bytes":7340032}}'
```

**With Cronitor-style URLs** (`/cronitor/p/<key>/<monitor>`), use `metric=name:value`, as with Cronitor.
Cronitor monitors imported into JobWatch keep their per-run assertions as run checks.

## What you see
The run shows its metrics; a run that broke a check shows **"Exited successfully but failed its run
checks"** with each broken rule and the value observed, e.g. `metric.size_bytes > 0 (was 0)`.
Failed runs (non-zero exit) are not re-checked: they have already failed.
