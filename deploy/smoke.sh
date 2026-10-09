#!/usr/bin/env bash
# R49 post-deploy smoke test: smoke.sh https://app.example.com https://ingest.example.com
# Optional SMOKE_PING_TOKEN: ping token of a job kept for this purpose (proves a ping is accepted
# end to end through the ingest domain, not just that the process answers).
set -uo pipefail
APP=${1:?app URL}; INGEST=${2:?ingest URL}; fail=0
check() {  # name, url, expected status, optional body substring
  local code body
  for _ in 1 2 3 4 5 6; do
    body=$(curl -sS -m 10 -w '\n%{http_code}' "$2" 2>/dev/null) && code=${body##*$'\n'} && body=${body%$'\n'*}
    [ "${code:-0}" = "$3" ] && { [ -z "${4:-}" ] || grep -q "$4" <<<"$body"; } && { echo "ok   $1"; return; }
    sleep 10
  done
  echo "FAIL $1 (wanted $3${4:+ containing '$4'}, got ${code:-no response})"; fail=1
}
# The app host routes /api, /auth, /public and /scim to the API; everything else to the web app.
check "API reachable"         "$APP/auth/session"     401
check "API uses the database" "$APP/public/status/smoke-missing-$(date +%s)" 404
check "SCIM routed to the API" "$APP/scim/v2/ServiceProviderConfig" 200 urn:ietf:params:scim
check "web app"               "$APP/welcome"          200 JobWatch
check "ingest health"         "$INGEST/healthz"       200 ok
check "ingest serves pings"   "$INGEST/ping/smoke-unknown-token-$(date +%s)" 404
check "Healthchecks paths"    "$INGEST/hc/00000000-0000-0000-0000-000000000000" 404
# R62: the agent installer is served with this deployment's defaults (deploy.yml sets them)
check "agent installer"       "$INGEST/agent/install.sh" 200 "DEFAULT_SERVER=\"$INGEST\""
if [ -n "${SMOKE_PING_TOKEN:-}" ]; then
  check "ping accepted end to end" "$INGEST/ping/$SMOKE_PING_TOKEN" 200
fi
exit $fail
