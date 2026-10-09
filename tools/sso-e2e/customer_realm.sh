#!/usr/bin/env bash
# Creates realm `acme-idp` (a fake customer identity provider) with users and OIDC + SAML clients
# that trust the JobWatch realm's broker. Idempotent enough for repeated staging runs.
set -euo pipefail
: "${KEYCLOAK_URL:?}" "${KEYCLOAK_ADMIN_PASSWORD:?}"
K=${KCADM:-kcadm.sh}; R=acme-idp; JW=${JW_REALM:-cronsentinel}
$K config credentials --server "$KEYCLOAK_URL" --realm master --user "${KEYCLOAK_ADMIN_USER:-admin}" --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null
$K get realms/$R >/dev/null 2>&1 || $K create realms -s realm=$R -s enabled=true
mk() { $K create users -r $R -s username=$1 -s email=$2 -s emailVerified=true -s firstName=$3 -s lastName=Test -s enabled=true >/dev/null 2>&1 || true
       $K set-password -r $R --username $1 --new-password "$4" >/dev/null; }
mk alice alice@acme.test Alice 'Alice-pass-1'
mk mallory victim@other-corp.test Mallory 'Mallory-pass-1'     # asserts an address the org does not own
mk bob bob@acme.test Bob 'Bob-pass-1'
mk carol carol@acme-saml.test Carol 'Carol-pass-1'
$K get clients -r $R -q clientId=jobwatch-broker --fields id | grep -q id || \
  $K create clients -r $R -s clientId=jobwatch-broker -s enabled=true -s publicClient=false -s secret=broker-secret-123 \
     -s "redirectUris=[\"$KEYCLOAK_URL/realms/$JW/broker/*\"]" -s standardFlowEnabled=true
SP="$KEYCLOAK_URL/realms/$JW"
$K get clients -r $R -q clientId="$SP" --fields id | grep -q id || $K create clients -r $R -f - <<JSON
{"clientId":"$SP","protocol":"saml","enabled":true,"redirectUris":["$KEYCLOAK_URL/realms/$JW/broker/*"],
 "attributes":{"saml.assertion.signature":"true","saml.server.signature":"true","saml.client.signature":"false",
   "saml_force_name_id_format":"true","saml_name_id_format":"email","saml.authnstatement":"true"},
 "protocolMappers":[
  {"name":"email","protocol":"saml","protocolMapper":"saml-user-property-mapper","config":{"user.attribute":"email","attribute.name":"email","attribute.nameformat":"Basic"}},
  {"name":"firstName","protocol":"saml","protocolMapper":"saml-user-property-mapper","config":{"user.attribute":"firstName","attribute.name":"firstName","attribute.nameformat":"Basic"}},
  {"name":"lastName","protocol":"saml","protocolMapper":"saml-user-property-mapper","config":{"user.attribute":"lastName","attribute.name":"lastName","attribute.nameformat":"Basic"}}]}
JSON
# Start each run clean: remove brokered copies of the test users from the JobWatch realm, so Keycloak
# treats every sign-in as a first broker login (leftovers trigger its "account already exists" page).
for e in alice@acme.test bob@acme.test carol@acme-saml.test victim@other-corp.test; do
  for id in $($K get users -r $JW -q email=$e --fields id --format csv --noquotes 2>/dev/null); do $K delete users/$id -r $JW; done
done
echo "customer realm '$R' ready"
