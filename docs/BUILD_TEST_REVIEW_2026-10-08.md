# Build, test, and missing-parts review — 2026-10-08

Reviewed the local JobWatch source on macOS ARM64. This directory has no Git metadata, so no branch or commit can be attributed to these results.

## Confirmed missing parts

- Private status-page access: `apps/api/cronsentinel/routers/status_pages.py` returns 404 for non-public pages; signed-link access remains unimplemented.
- SMS notifications: `apps/api/cronsentinel/alerting/tasks.py` has no SMS sender; the provider is still undecided.
- Discovery reconciliation: `apps/api/cronsentinel/routers/agents.py` does not flag jobs that disappear from a subsequent discovery report, and has no discovery-skipped counter for plan limits.
- Worker lag metrics: `apps/api/cronsentinel/main.py` still lacks the planned ingest/reconciler lag gauges.
- Release delivery: `.github/workflows/ci.yml` builds container images but leaves registry login/push, Helm packaging, and agent release assets as TODOs.

These are source-confirmed gaps, not a claim that all historical TODOs remain valid. Architecture/development documents contain older descriptions superseded by implementation (for example, the worker database session now switches to `jobwatch_system`).

## Fixes made

- Linux-agent ownership test: derive the expected account name from the host's UID lookup instead of assuming UID 65534 is always named `nobody`. The forged-root-fingerprint assertion remains enforced.
- Python HTTP test servers: consume POST bodies and declare response lengths before closing connections. Unread request data caused intermittent connection-reset failures on macOS. Production request-guard code is unchanged.
- Generated dependency checksums with `go mod tidy` for the Kubernetes agent and Terraform provider, matching their CI setup steps.

## Verification

| Component | Result |
| --- | --- |
| Python lint | `ruff check .` passed |
| Python tests excluding `tests/integration` | 251 passed, 33 skipped after fixture fixes |
| Request-guard focused tests | 42 passed |
| Python source compilation | Passed |
| Python packaging | Source distribution and wheel built in `/tmp/jobwatch-api-dist` |
| Linux agent | Tests and vet passed; Linux amd64 and arm64 binaries built |
| Kubernetes agent | Tests, vet, and build passed |
| Terraform provider | Tests, vet, and build passed |
| Main Helm chart | Repository mini-helm renderer validated Kubernetes YAML |
| Agent Helm chart | Render passed with bootstrap token; missing token rejected as expected |

The 33 skipped Python tests comprise 9 full-stack tests, 23 requiring `systemd-analyze`, and 1 PostgreSQL tenant-isolation test. Database integration tests were excluded entirely. Docker's status request did not respond; no local PostgreSQL binary was found. Consequently database migrations, real-service integration, container builds, and full-stack behavior are not verified.

Web dependency installation encountered certificate errors and download timeouts. Direct verified-TLS downloads from the configured npm mirror also timed out with incomplete archives. Next.js 15.5.27, its macOS ARM64 SWC compiler, and the libvips archive remained unavailable. The stalled npm install was stopped. Web build, typecheck, unit, and browser tests could not run; none are claimed as passing.

To finish verification, restore reliable package downloads and a working Docker/PostgreSQL environment, then run the web and service-backed checks in `.github/workflows/ci.yml`. The local web `node_modules` directory is incomplete and needs a successful `npm ci` before use.

Follow-up recheck: the Docker context is `desktop-linux`. Starting the installed Docker Desktop application did not restore an engine response (an 8-second bounded probe still timed out). UI inspection also timed out. Resuming the Next.js download transferred only another 127,020 bytes before a 30-second timeout, so the archive remains incomplete.

Toolchain: Python 3.12.12, Node 26.9.0, Go 1.27.1. CI specifies Node 22 and Go 1.23, so local results do not prove those exact CI environments.
