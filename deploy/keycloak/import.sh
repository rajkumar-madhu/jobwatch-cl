#!/usr/bin/env bash
# R35 — one-shot import of cronsentinel-realm.json, for the Keycloak deploy that was always a
# stubbed issuer URL (config.py's keycloak_issuer default) with nothing behind it.
#
# What this does and does not set up:
#   - Creates (or updates, if already there) the "cronsentinel" realm and its one client.
#   - Rotates the client secret and prints it — put it straight into KEYCLOAK_CLIENT_SECRET.
#   - Does NOT create any users, groups, or roles. JobWatch does not read them: every
#     authorization decision comes from the app's own `memberships` table (see
#     cronsentinel/routers/auth.py — the callback looks up org_id/role by email, never by a
#     Keycloak claim). Keycloak's only job here is proving identity (sub, email, name); teach
#     people to sign up through the app's own /auth/signup, not through a Keycloak admin console.
#
# Usage:
#   KEYCLOAK_URL=https://sso.example.com \
#   KEYCLOAK_ADMIN_PASSWORD=... \
#   API_PUBLIC_URL=https://api.jobwatch.example.com \
#   WEB_PUBLIC_URL=https://app.jobwatch.example.com \
#   ./import.sh
#
# Requires kcadm.sh on PATH (ships inside every Keycloak container at
# /opt/keycloak/bin/kcadm.sh — mount or exec into it if running via compose/Helm).
set -euo pipefail
cd "$(dirname "$0")"

: "${KEYCLOAK_URL:?e.g. https://sso.example.com (no trailing slash)}"
: "${KEYCLOAK_ADMIN_PASSWORD:?the realm-master admin password}"
KEYCLOAK_ADMIN_USER=${KEYCLOAK_ADMIN_USER:-admin}
API_PUBLIC_URL=${API_PUBLIC_URL:-http://localhost:8000}
WEB_PUBLIC_URL=${WEB_PUBLIC_URL:-http://localhost:3000}
KCADM=${KCADM:-kcadm.sh}

command -v "$KCADM" >/dev/null || { echo "kcadm.sh not found — set KCADM=/path/to/kcadm.sh"; exit 1; }

echo "== rendering realm export with real redirect/logout URIs =="
REALM_JSON=$(mktemp)
trap 'rm -f "$REALM_JSON"' EXIT
python3 - "$API_PUBLIC_URL" "$WEB_PUBLIC_URL" <<'PY' > "$REALM_JSON"
import json, sys
api, web = sys.argv[1], sys.argv[2]
doc = json.load(open("cronsentinel-realm.json"))
doc["clients"][0]["redirectUris"] = [f"{api}/auth/callback"]
doc["clients"][0]["attributes"]["post.logout.redirect.uris"] = web
# a self-hosted deploy behind its own TLS terminator can safely stay http internally; only
# require external TLS when the public URL itself is https, so local/dev imports still work.
doc["sslRequired"] = "external" if api.startswith("https://") else "none"
print(json.dumps(doc, indent=2))
PY

echo "== authenticating to $KEYCLOAK_URL =="
"$KCADM" config credentials --server "$KEYCLOAK_URL" --realm master --user "$KEYCLOAK_ADMIN_USER" --password "$KEYCLOAK_ADMIN_PASSWORD"

if "$KCADM" get realms/cronsentinel >/dev/null 2>&1; then
  echo "== realm exists — updating in place (create-or-update, not destructive) =="
  "$KCADM" update realms/cronsentinel -f "$REALM_JSON"
  CID=$("$KCADM" get clients -r cronsentinel -q clientId=cronsentinel-web --fields id --format csv --noquotes | tail -1)
  "$KCADM" update "clients/$CID" -r cronsentinel -f - <<< "$(python3 -c "import json;print(json.dumps(json.load(open('$REALM_JSON'))['clients'][0]))")"
else
  echo "== creating realm =="
  "$KCADM" create realms -f "$REALM_JSON"
  CID=$("$KCADM" get clients -r cronsentinel -q clientId=cronsentinel-web --fields id --format csv --noquotes | tail -1)
fi

echo "== rotating client secret =="
SECRET=$("$KCADM" get "clients/$CID/client-secret" -r cronsentinel --fields value --format csv --noquotes 2>/dev/null || true)
if [[ -z "$SECRET" ]]; then
  "$KCADM" create "clients/$CID/client-secret" -r cronsentinel >/dev/null
  SECRET=$("$KCADM" get "clients/$CID/client-secret" -r cronsentinel --fields value --format csv --noquotes)
fi

echo "== R51: self-sign-up and email (verification mails) =="
# Registration is on in the realm file; it only works if Keycloak can send the verification email.
if [[ -n "${KEYCLOAK_SMTP_HOST:-}" ]]; then
  : "${KEYCLOAK_SMTP_FROM:?set KEYCLOAK_SMTP_FROM, e.g. no-reply@example.com}"
  # smtpServer is a string map: kcadm's dotted -s smtpServer.x=… is silently ignored, so send JSON.
  SMTP_JSON=$(python3 -c '
import json, os
e = os.environ.get
cfg = {"host": e("KEYCLOAK_SMTP_HOST"), "port": e("KEYCLOAK_SMTP_PORT", "587"), "from": e("KEYCLOAK_SMTP_FROM"),
       "fromDisplayName": e("KEYCLOAK_SMTP_FROM_NAME", "JobWatch"), "starttls": e("KEYCLOAK_SMTP_STARTTLS", "true"),
       "ssl": e("KEYCLOAK_SMTP_SSL", "false"), "auth": "true" if e("KEYCLOAK_SMTP_USER") else "false"}
if e("KEYCLOAK_SMTP_USER"):
    cfg.update(user=e("KEYCLOAK_SMTP_USER"), password=e("KEYCLOAK_SMTP_PASSWORD", ""))
print(json.dumps({"smtpServer": cfg}))')
  "$KCADM" update realms/cronsentinel -f - <<< "$SMTP_JSON"
  "$KCADM" update realms/cronsentinel -s registrationAllowed=true -s registrationEmailAsUsername=true -s verifyEmail=true
else
  echo "WARNING: KEYCLOAK_SMTP_HOST is not set. Sign-up stays OFF: new accounts must verify their email and"
  echo "         Keycloak cannot send that mail. Set KEYCLOAK_SMTP_HOST/PORT/FROM (and USER/PASSWORD) and re-run."
  "$KCADM" update realms/cronsentinel -s registrationAllowed=false
fi

echo "== R43: SSO admin client (identity providers only) =="
# The realm update above does not create clients/users on an existing realm, so ensure them here.
ACID=$("$KCADM" get clients -r cronsentinel -q clientId=jobwatch-admin --fields id --format csv --noquotes | tail -1)
if [[ -z "$ACID" ]]; then
  "$KCADM" create clients -r cronsentinel -f - <<< "$(python3 -c "import json;print(json.dumps([c for c in json.load(open('$REALM_JSON'))['clients'] if c['clientId']=='jobwatch-admin'][0]))")"
  ACID=$("$KCADM" get clients -r cronsentinel -q clientId=jobwatch-admin --fields id --format csv --noquotes | tail -1)
fi
"$KCADM" add-roles -r cronsentinel --uusername service-account-jobwatch-admin --cclientid realm-management \
  --rolename manage-identity-providers --rolename view-identity-providers
ADMIN_SECRET=$("$KCADM" get "clients/$ACID/client-secret" -r cronsentinel --fields value --format csv --noquotes 2>/dev/null || true)
if [[ -z "$ADMIN_SECRET" ]]; then
  "$KCADM" create "clients/$ACID/client-secret" -r cronsentinel >/dev/null
  ADMIN_SECRET=$("$KCADM" get "clients/$ACID/client-secret" -r cronsentinel --fields value --format csv --noquotes)
fi

cat <<EOF

== done ==
Set these on the API deployment:
  KEYCLOAK_ISSUER=$KEYCLOAK_URL/realms/cronsentinel
  KEYCLOAK_CLIENT_ID=cronsentinel-web
  KEYCLOAK_CLIENT_SECRET=$SECRET
  KEYCLOAK_ADMIN_CLIENT_ID=jobwatch-admin
  KEYCLOAK_ADMIN_CLIENT_SECRET=$ADMIN_SECRET      # enables per-organization single sign-on
  API_PUBLIC_URL=$API_PUBLIC_URL
  WEB_PUBLIC_URL=$WEB_PUBLIC_URL

No users, groups or roles were created. First login for each person still goes through the app's
own /auth/login → Keycloak → /auth/callback → the app's own org signup/invite flow — Keycloak only
has to know their email exists (or let them register one, if you enable that in the realm).
EOF
