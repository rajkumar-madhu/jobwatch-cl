# Security overview

How JobWatch protects your data and your systems. Engineering detail lives in the repository.

## Tenant isolation
Every row belongs to an organization, and PostgreSQL row-level security is enforced on every tenant
table: the database itself refuses to return another organization's data, even if application code
asks for it. Background workers use a separate role.

## Access control
Six roles (viewer, developer, sre, devops, admin, owner), checked on every API call. Single sign-on
with verified domains, optional enforcement (owners keep a break-glass password), and SCIM so people
lose access the moment your directory removes them. Every change is in the audit log, which can be
streamed to your SIEM.

## Secrets
- **Ping tokens, API keys and agent keys are stored only as hashes.** Ping URLs and keys are shown
  once; a lost one is replaced, not recovered.
- Alert-channel credentials, identity-provider secrets and integration keys are encrypted at rest and
  never returned by the API.
- Run output is **redacted** (passwords, tokens, keys, connection strings, private keys) before it is
  stored, before it appears in alerts and before the AI copilot sees it.

## Sessions
Session cookies are httpOnly, SameSite=Lax and Secure; state-changing requests from the browser need a
CSRF token; sign-out is protected the same way.

## Outbound calls
Webhooks, SMTP servers, SIEM endpoints and import sources are resolved and checked before connecting,
so nobody can point JobWatch at internal addresses; TLS certificates are verified. Datadog, PagerDuty
and Opsgenie are called only on their own fixed hosts.

## Agents
- The installer verifies the agent against the release checksums before installing anything.
- Agents report environment variable **names** from an allow-list, never values.
- The Linux agent runs as a hardened systemd service; the Kubernetes agent has read-only access.
- Agents buffer on disk and authenticate with their own revocable keys.

## AI copilot
Read-only: it reads redacted telemetry and suggests commands as text; nothing is ever executed. It can
use a model you host, so no data leaves your network.

## Self-hosting
The whole platform runs in your own Kubernetes cluster with the Helm chart, if your data has to stay
in your network. See [Self-hosting](deploy).
