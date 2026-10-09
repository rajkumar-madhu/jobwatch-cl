#!/usr/bin/env bash
# R64 end-to-end check of the provider against a real JobWatch API, with OpenTofu or Terraform.
#   PROVIDER_BIN=./terraform-provider-jobwatch TF=tofu JOBWATCH_API_URL=… JOBWATCH_API_KEY=… ./run.sh
# The API needs INGEST_PUBLIC_URL set (ping_url) and the key's org a workspace. Everything it creates
# is destroyed at the end.
set -euo pipefail
: "${PROVIDER_BIN:?}" "${TF:?}" "${JOBWATCH_API_URL:?}" "${JOBWATCH_API_KEY:?}"
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
REG=registry.opentofu.org; "$TF" version | grep -qi terraform && REG=registry.terraform.io
mkdir -p "$W/mirror/$REG/wecrew/jobwatch/0.1.0/linux_amd64"
cp "$PROVIDER_BIN" "$W/mirror/$REG/wecrew/jobwatch/0.1.0/linux_amd64/terraform-provider-jobwatch_v0.1.0"
printf 'provider_installation {\n  filesystem_mirror {\n    path = "%s"\n    include = ["%s/wecrew/*"]\n  }\n}\n' "$W/mirror" "$REG" > "$W/rc"
export TF_CLI_CONFIG_FILE="$W/rc"
cp "$(dirname "$0")/main.tf" "$W/main.tf"; cd "$W"
api() { curl -s -H "X-API-Key: $JOBWATCH_API_KEY" "$JOBWATCH_API_URL$1"; }
code() { curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $JOBWATCH_API_KEY" "$JOBWATCH_API_URL$1"; }
plan_is() { set +e; "$TF" plan -detailed-exitcode -no-color > plan.out 2>&1; rc=$?; set -e
            [ "$rc" = "$1" ] || { echo "FAIL $2: plan exit $rc, wanted $1"; cat plan.out; exit 1; }; echo "ok   $2"; }
id_of() { "$TF" state show -no-color "$1" | grep -E '^\s+id ' | head -1 | cut -d'"' -f2; }

"$TF" init -no-color > /dev/null
"$TF" apply -auto-approve -no-color > /dev/null; echo "ok   apply creates a job and a weekly window"
plan_is 0 "no changes right after apply (spaced run check, +05:30 times, lowercase weekday)"
JOB=$(id_of jobwatch_job.backup); MW=$(id_of jobwatch_maintenance_window.patching)
"$TF" output -raw ping_url | grep -q '/ping/' && echo "ok   ping_url captured (sensitive)"
api "/api/v1/jobs/$JOB" | grep -q '"assertions":\["metric.size_bytes > 0","duration < 2h"\]' && echo "ok   run checks stored"

sed -i 's/"0 2 \* \* \*"/"30 1 * * *"/; s/\["db", "prod"\]/["db", "prod", "payments"]/; s/weekdays  = \["su"\]/weekdays  = ["SA", "SU"]/' main.tf
sed -i 's/^  expected_runtime_seconds = 3600$/  paused                   = true/' main.tf
"$TF" apply -auto-approve -no-color > apply2.out
grep -q "1 added, 1 changed, 1 destroyed" apply2.out && echo "ok   update in place; weekday change replaces the window"
[ "$(code "/api/v1/alerts/maintenance/$MW")" = 404 ] && echo "ok   replaced window deleted"
api "/api/v1/jobs/$JOB" | grep -q '"expected_runtime_s":null' && echo "ok   removed attribute cleared"
plan_is 0 "no changes after update"

curl -s -o /dev/null -X PATCH -H "X-API-Key: $JOBWATCH_API_KEY" -H 'Content-Type: application/json' -d '{"paused": false, "tags": ["db"]}' "$JOBWATCH_API_URL/api/v1/jobs/$JOB"
plan_is 2 "drift made outside Terraform is detected"
"$TF" apply -auto-approve -no-color > /dev/null; plan_is 0 "re-apply repairs drift"

"$TF" state rm -no-color jobwatch_job.backup > /dev/null; "$TF" import -no-color jobwatch_job.backup "$JOB" > /dev/null
plan_is 0 "no changes after import (server-normalised run checks, no ping_url)"

MW=$(id_of jobwatch_maintenance_window.patching)
"$TF" destroy -auto-approve -no-color > /dev/null
[ "$(code "/api/v1/jobs/$JOB")" = 404 ] && [ "$(code "/api/v1/alerts/maintenance/$MW")" = 404 ] && echo "ok   destroy removes both"
echo "ALL CHECKS PASSED"
