# Deploying JobWatch (R49)

Pushes a version tag → GitHub Actions builds three images, deploys to Kubernetes with Helm, runs the
database migrations first, smoke-tests the live URLs and rolls back if the smoke test fails.

## What you need
- A Kubernetes cluster with **ingress-nginx** and cert-manager (or another TLS source for the two
  hosts), plus Postgres 16, Redis ≥ 6.2 and Keycloak 25+ reachable from it. NATS is installed by the chart.
- Two DNS names per environment pointing at the ingress: the app host (UI + API) and the ingest host
  (pings and agents). Keep them separate: they have different edge rate limits.

## One-time setup
1. **Push the code.** `git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main`.
   CI (`ci.yml`) runs on every push, including the Kubernetes agent's first real build in CI.
2. **Keycloak.** Run `deploy/keycloak/import.sh` against your Keycloak; note the two client secrets it prints.
3. **Database.** Create the database and an owner role. Migrations (run by a Helm pre-upgrade hook as
   that owner) create the `jobwatch_app` role the services use, with the password you choose.
4. **GitHub Environments** (Settings → Environments): create `staging` and `production`; give
   production required reviewers. In each, set:

   | Variables | Example |
   |---|---|
   | `DOMAIN` | `jobwatch-staging.example.com` |
   | `INGEST_DOMAIN` | `ingest.jobwatch-staging.example.com` |
   | `KEYCLOAK_ISSUER` | `https://sso.example.com/realms/cronsentinel` |
   | `NAMESPACE`, `RELEASE` (optional) | `jobwatch` |

   | Secrets | Notes |
   |---|---|
   | `KUBECONFIG_B64` | `base64 -w0 kubeconfig` of a **namespace-scoped** deploy service account |
   | `DATABASE_URL` | `postgresql+psycopg://jobwatch_app:<app password>@host:5432/jobwatch` |
   | `MIGRATION_DATABASE_URL` | same database, the **owner** role |
   | `APP_DB_PASSWORD` | the `<app password>` above |
   | `SECRET_ENCRYPTION_KEY` | `openssl rand -base64 48` — **never change it afterwards** (sessions and stored channel secrets are keyed off it) |
   | `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_ADMIN_CLIENT_SECRET` | from step 2 |
   | `REDIS_URL` (optional) | default `redis://redis:6379/0` |
   | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (optional) | billing |
   | `SMOKE_PING_TOKEN` (optional) | ping token of a job kept for smoke tests (create it after the first deploy) |

## Sign-up and sign-in options (R51)
- Email sign-up uses Keycloak's registration form (email verification is on in the realm export).
- "Continue with Google / GitHub / Microsoft" buttons appear only for providers listed in
  `env.AUTH_SOCIAL_PROVIDERS` (Helm values or `deploy/environments/*.yaml`). Add each one as an
  identity provider in the Keycloak realm first, with the same alias.
- Optional build variable `NEXT_PUBLIC_SALES_EMAIL` turns the Enterprise "Talk to us" button into a
  mail link; without it the button goes to sign-up.

## Deploying
- **Staging:** `git tag v0.2.0 && git push origin v0.2.0`. The tag also publishes the Linux agent
  binaries and `SHA256SUMS` to the GitHub release (what `install.sh` downloads).
- **Production:** Actions → deploy → Run workflow → `production`, after staging looks right. Waits for a reviewer.
- Non-secret per-environment settings (replica counts, NATS size) live in `deploy/environments/*.yaml`.

## After the first staging deploy
1. Sign in, create a job, put its ping token in `SMOKE_PING_TOKEN`.
2. `tools/sso-e2e` against the staging Keycloak (SSO and SCIM, OIDC and SAML).
3. `tools/loadtest` against staging to get real capacity numbers for your nodes.
4. One real sign-in for each identity provider a customer will use (Okta, Entra ID, ADFS).

## Rollback
The workflow rolls back the application automatically when the smoke test fails. By hand:
`helm rollback <release> -n <namespace>`.

**Database migrations are not rolled back**, and not every migration is backward compatible.
Rolling the app back is safe only if the previous release works with the new schema. Known
exception: **0016** (hashed ping tokens) drops the plaintext token column, so a release before R41
cannot run on a database at 0016 or later. Before deploying a release whose changelog lists a
migration that drops or renames something, take a database backup; the backup is the rollback.


## Agent installer (R62)
The ingest host serves the Linux agent installer at `/agent/install.sh`, filled in with
`AGENT_DOWNLOAD_BASE`, `AGENT_VERSION` and `INGEST_PUBLIC_URL`. The deploy workflow sets the first two
to the GitHub release of the deployed tag, where the release job uploads the agent binaries and
`SHA256SUMS`. Values are validated strictly (plain `https://` URLs, a tag-like version); anything else
and the endpoint answers 503 instead of serving a script. The smoke test checks that it is served.
