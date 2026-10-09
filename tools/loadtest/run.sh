#!/usr/bin/env bash
# Ingest load test with exact loss accounting (R47). Needs Postgres, Redis and NATS reachable with the
# API's env (DATABASE_URL, REDIS_URL, NATS_URL, SECRET_ENCRYPTION_KEY…), Go, and apps/api installed.
# Seeds a fresh org with $JOBS heartbeat jobs, starts the full stack, runs the load at each
# concurrency in $CONC for $DUR, waits for processing to drain, and checks that every accepted ping
# became exactly one execution. Exits non-zero if anything was lost.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); API=$HERE/../../apps/api
CONC=${CONC:-"20 100"}; DUR=${DUR:-15s}; INGEST=${INGEST:-http://127.0.0.1:18010}
(cd "$HERE" && go build -o hammer .)
cd "$API"
(cd "$HERE" && python3 seed.py)
ORG=$(cat "$HERE/org.txt")
bash scripts/fullstack.sh up >/dev/null
trap 'bash scripts/fullstack.sh down >/dev/null 2>&1' EXIT
for _ in $(seq 1 40); do curl -sf -m 1 -o /dev/null "$INGEST/healthz" && break; sleep 1; done
q() { python3 -c "from sqlalchemy import text; from cronsentinel.db import system_session
with system_session() as s: print(s.execute(text(\"$1\")).scalar())"; }
accepted=0
for c in $CONC; do
  out=$(cd "$HERE" && ./hammer -base "$INGEST" -c "$c" -d "$DUR"); echo "c=$c $out"
  n=$(sed -n 's/.*map\[.*200:\([0-9]*\).*/\1/p' <<<"$out"); accepted=$((accepted + ${n:-0}))
done
prev=-1
for _ in $(seq 1 90); do
  cur=$(q "SELECT count(*) FROM executions WHERE org_id='$ORG'"); [ "$cur" = "$prev" ] && break; prev=$cur; sleep 2
done
echo "accepted (HTTP 200): $accepted   executions recorded: $cur"
if [ "$cur" -eq "$accepted" ]; then echo "no loss, no duplicates"; else echo "MISMATCH"; exit 1; fi
