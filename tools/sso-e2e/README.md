# Live SSO + SCIM end-to-end check (R45)

Proves single sign-on and directory sync against a **real Keycloak**, with a second Keycloak realm
standing in for a customer's identity provider. This is how R43/R44 were verified; run it on
staging after upgrading Keycloak or changing `deploy/keycloak/*`.

What it checks:
1. OIDC: a user is routed by email domain to the customer IdP, signs in, and lands in the right org
   with the default role (just-in-time membership).
2. Security: an IdP user asserting an email outside the org's verified domains is refused (403).
3. SCIM + SSO: a user provisioned over SCIM links to the same record on first SSO sign-in and keeps
   the role set in JobWatch; after SCIM deactivation, SSO sign-in is refused.
4. SAML: the same customer realm as a SAML IdP with signed assertions; attributes map to the user.

## Run
```bash
# 1. Keycloak with the JobWatch realm (prints the env vars to export)
KCADM=/opt/keycloak/bin/kcadm.sh KEYCLOAK_URL=http://localhost:8080 KEYCLOAK_ADMIN_PASSWORD=… \
  bash deploy/keycloak/import.sh
# 2. A fake customer IdP realm with test users
KCADM=/opt/keycloak/bin/kcadm.sh KEYCLOAK_URL=http://localhost:8080 KEYCLOAK_ADMIN_PASSWORD=… \
  bash tools/sso-e2e/customer_realm.sh
# 3. API running with the step-1 env vars, then:
pip install playwright && playwright install chromium
python tools/sso-e2e/run.py          # exits non-zero on any failed check
```
Test-only data: it creates orgs named "SSO e2e …" and domains `acme.test` / `acme-saml.test`.
Never point it at production.
