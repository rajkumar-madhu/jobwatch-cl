# R66 — on-call schedules and escalation policies

JobWatch now decides who gets paged and keeps paging until someone acknowledges, instead of only
handing off to PagerDuty or Opsgenie.

## Upgrade notes
- **Migration 0026**: `oncall_schedules`, `oncall_layers`, `oncall_overrides`, `escalation_policies`,
  `escalation_steps`, `escalation_runs` (row-level security); plan features `oncall` and `escalation`
  on Team and Business.
- No new service: the escalation loop runs in the outbound-exporter worker (every 15 s).

## What ships
- **Schedules** (`cronsentinel/oncall.py`):
  - **Layers:** rotations daily, weekly or every N hours over an ordered list of people, from a handoff
    time. Daily and weekly handoffs keep local time across daylight saving.
  - **Restrictions:** a layer can be limited to weekdays and a time of day, including overnight windows,
    which belong to the day they start. The highest active layer wins.
  - **Overrides** beat every layer.
  - **"Who is on call now"** and a merged timeline of upcoming shifts.
- **Escalation policies** (`cronsentinel/escalation.py`):
  - **Steps:** each step pages whoever is on call in some schedules and/or named people, then waits
    1–1440 minutes for an acknowledgement before the next step.
  - **Repeat** 0–9 times, then the run is "exhausted".
  - **Delivery** through an existing email, Slack, Teams, Discord, Telegram or webhook channel. Email
    goes to the paged people's own addresses. Every page carries the full alert context and an
    acknowledge link.
- **Wiring:**
  - A new channel kind `escalation` (or **Route alerts here** on a policy) lets alert rules page on-call.
  - One escalation per incident (or per job and condition); a repeated alert never starts a second one.
  - **Stopping:** acknowledging the page (which also acknowledges its incident) or the incident stops
    paging; a resolved incident or a recovered job stops it too.
  - A schedule with nobody on call is recorded in the page history and the policy moves on.
- **API** `/api/v1/oncall/…`: schedules (layers, overrides, `?days=` timeline), policies,
  escalations, acknowledge, people, policy → channel. Everything is audited.
- **On-call page** (sidebar): paging now with Acknowledge (page links highlight theirs), schedules with
  who is on call and upcoming shifts, overrides, new schedule/policy forms.
- Docs: "On-call schedules and escalation". `/compare`: the "behind" item is now the honest remaining
  gap: no phone calls, SMS or mobile app.

## Fixed along the way
- **Deleting an alert channel that a policy delivers through** returned a 500 (a database
  constraint). It now answers 409 and names the policy. Found by the router sweep once its seed included a policy.
- `test_slot_inside_a_recorded_gap_is_unobserved_not_missed` asserted that its slot was the only one in
  the shared test database. A JobWatch-side gap is platform-wide, so other tests' slots counted too.
  It now checks its own slot exactly and that the gap counted it.
- Test fixtures now use full-length random emails (4–6 hex characters collided across runs).

## Verification
- 13 unit tests for who is on call: rotations, the daylight-saving handoff, business-hours over 24/7, a
  Friday-night window into Saturday, overrides, the merged timeline, validation.
- 9 integration tests through the real notifier and engine: step 1 at once, nothing before the delay,
  step 2 after it, exhausted afterwards; acknowledging stops paging and acknowledges the incident; an
  incident acknowledged elsewhere and a recovered job stop paging; repeats; nobody on call; no double
  paging for a repeated alert; roles, validation, plan gate; Route alerts here really pages; a
  delivery channel in use cannot be deleted. Mutation-checked: without the stop-on-acknowledged rule or
  the one-run-per-alert guard, tests fail.
- Router sweep now seeds a member, channel, schedule and policy, so on-call routes run against real rows.
- Backend 895 passed / 0 failed; full stack 6/6 with a clean outbound-exporter log; Playwright 96 / 0;
  all pages fit 390 px in light and dark.

---

# R65 — Kubernetes: configure monitoring with annotations; CronJob time zones honoured

## Upgrade notes
- **Migration 0025**: `k8s_cronjobs.annotation_warnings`, `k8s_cronjobs.time_zone`.
- **Upgrade the Kubernetes agent** to report time zones and annotations; older agents keep working as before.

## What ships
- **`jobwatch.io/*` annotations** on a CronJob (`cronsentinel/k8sannotations.py`): `name`, `grace`,
  `expected-runtime` (`90`, `10m`, `1h30m`; up to 7 days), `tags`, `run-checks` (`;`-separated),
  `description`, `ignore`.
  - **A present annotation is the source of truth**: it is reapplied on every discovery. An absent one
    leaves app edits alone.
  - **Invalid values are not applied**, and each produces a warning shown next to the CronJob.
  - **`ignore`** keeps a new CronJob unmonitored (listed as *not monitored*). On a monitored one it
    pauses the job and keeps its history; removing it resumes monitoring.
  - **A name already taken** by another job is not applied; a warning says so. Before, `ON CONFLICT`
    could have attached the CronJob to the other job.
- **Bug fixed: `spec.timeZone` was ignored**, so a `0 2 * * *` CronJob in `Asia/Kolkata` was expected at
  02:00 UTC. The agent now reports it; the job gets the zone and its next run in it.
- **When a CronJob's schedule or time zone changes**, pending runs generated for the old one are
  dropped and generation restarts from now (before, they could be reported missed).
- **Agent privacy**: only `jobwatch.io/*` annotations are sent (at most 20, values capped). Others,
  such as `last-applied-configuration`, can contain secrets and never leave the cluster.
- **Kubernetes page**: time zone under the schedule, annotation warnings, a *not monitored* badge.
- **Bug fixed on phones**: the CronJob table's fixed columns took ~626 of its 640 px minimum width,
  leaving the CronJob name and failure-reason columns at almost zero. CronJob names were invisible on
  phones. The table now has a 960 px minimum and scrolls; badges no longer get cut off by long names.
- Docs: Kubernetes guide gains "Configure monitoring on the CronJob".

## Verification
- Kubernetes agent built against client-go v0.31.3 (through GitHub mirrors in a throwaway copy); `go vet`
  clean; all tests pass, including a new one confirming a `last-applied-configuration` annotation
  containing a password is not reported.
- 3 parser unit tests; 5 integration tests through the real discovery endpoint: annotations configure a
  new job and 02:00 Kolkata lands at 20:30 UTC; present annotations win while absent ones keep app edits;
  invalid values (and an unknown time zone) warn without applying; ignore skips, pauses, then resumes;
  a time-zone change drops old pending runs. Mutation-checked: forcing UTC fails the time-zone test.
- Backend 829 passed / 0 failed; Playwright 90 passed / 0 failed (the new phone test found the
  zero-width column bug); all pages fit 390 px in light and dark.

---

# R64 — Terraform / OpenTofu provider

`integrations/terraform-provider-jobwatch` (Go, HashiCorp plugin framework v1.13): manage JobWatch as
code. Works with Terraform and OpenTofu.

## What ships
- **`jobwatch_job`**: name, description, schedule, timezone, grace, expected runtime, tags, run checks,
  paused, workspace (defaults to the first; changing it replaces the job). Computed **`ping_url`
  (sensitive)** and `ping_token_hint`. Import by job id.
- **`jobwatch_maintenance_window`**: name, start/end (RFC 3339, any offset), timezone, repeat none /
  daily / weekly with weekdays, scope by tags / job ids / environment ids. No in-place edit (the API
  has none): any change replaces the window. Import by id.
- **Clean plans by design**:
  - run checks that differ only in spacing are equal (JobWatch collapses spaces);
  - the same instant written with another offset is not a change;
  - weekday case and order are kept;
  - recurrence is read back from the stored rule, so real drift shows;
  - an imported job keeps an empty `ping_url` instead of planning an unknown one.
- Provider config: `api_url`, `api_key` (sensitive), or `JOBWATCH_API_URL` / `JOBWATCH_API_KEY`.
- **API additions:**
  - `ping_url` in job create/rotate responses (when `INGEST_PUBLIC_URL` is set; never on reads);
  - `GET /api/v1/alerts/maintenance/{id}`, which works after a window ended (the list hides those,
    which would have made Terraform recreate finished windows).
- Docs: `/docs/terraform` and the provider README. The landing page's integrations grid gains "As code".
- CI: a `terraform-provider` job runs `go mod tidy`, `go vet`, `go test` and builds the provider.
- **Not yet done:** publishing to the Terraform and OpenTofu registries (needs a signed release and a
  registry account). Until then, install from a filesystem mirror or `dev_overrides`.

## Verification
- **End to end with OpenTofu 1.8.5** (`e2e/run.sh`, kept in the repo) against the real API and database,
  the provider installed from a filesystem mirror. 12 checks: apply; a **clean plan** right after (with
  a spaced run check, `+05:30` times, lowercase weekday); ping URL captured; update in place; weekday change
  replaces the window and deletes the old one; removed attribute cleared; clean plan; drift made in the
  app detected; re-apply repairs it; **clean plan after import**; destroy removes both.
- The first runs found three bugs: a wrong workspaces path in the client, a pointless update after
  import (run-check spacing), and `ping_url` planned as unknown after import. All three are fixed, and
  the script covers them.
- Go: `go vet` clean, `gofmt` clean, unit tests for the plan-stability helpers.
- Backend 821 passed / 0 failed (new: `ping_url` only alongside the token; get window by id after it
  ended); Playwright 88 passed / 0 failed.
- Build note: the sandbox fetched Go modules through GitHub mirrors (`golang.org`, `google.golang.org`,
  `gopkg.in` blocked), via a throwaway copy with `replace` lines; the committed `go.mod` has none, and
  `go.sum` is generated by CI.

---

# R63 — maintenance windows finished, and three broken exporter queries fixed

## Upgrade notes
- **Migration 0024**: `maintenance_windows` gains `name`, `tz`, `created_by`, `created_at` and an
  end-after-start check (`NOT VALID`, so existing rows are not re-checked).
- No new services.

## Maintenance windows (existed in part; now complete)
Bugs found in what existed:
- **Recurring windows were accepted but ignored.** `rrule` was stored, but the alert engine had a
  `TODO: rrule expansion` and the reconciler only checked the first start/end. A weekly window covered
  its first occurrence only; from the second week, runs inside it were marked missed and alerted.
- Recurring windows **disappeared from the list** a week after their first occurrence.
- **Public status pages listed every window in the organization**, including ones covering only
  internal jobs, and did not expand recurring ones.
- The engine (Python) and reconciler (SQL) matched scope separately; deletes were not audited; no UI.

What ships:
- **`cronsentinel/maintenance.py`**, one definition for the engine, the reconciler and status pages:
  once / daily / weekly-on-days recurrence expanded **in the window's time zone** (keeps local time
  across daylight saving; on the repeated hour when clocks go back, the first one is used); scope =
  all jobs, job ids, tags or environments; up to 14 days (daily up to 24 hours).
- **API**: name, time zone, `repeat`/`weekdays`/`until` (or an advanced RRULE, DAILY/WEEKLY only),
  validation (422 with the reason), `active_now`, `current_end`, next occurrence, **End now** for one-off
  windows, audited create/end/delete.
- **Alerting page → Maintenance windows**: list (repeat, scope, time zone, next occurrence, Active now),
  schedule form (weekdays shown for weekly), End now, Delete.
- **Status pages** show only windows covering the page's public jobs, with expanded upcoming dates.
- **Docs**: new "Maintenance windows" page in /docs (Alerts and checks).

## Outbound exporter: three queries against columns that do not exist
Found while investigating the flaky full-stack test: the outbound-exporter log had an error every 15
seconds. The incident poll selected `incident_events.created_at` (the column is `ts`) and
`incidents.acked_by` (now the acknowledging event's actor); the agent poll selected `agents.host` (the
column is `host_id`). **Incident and agent-status signals had never reached signal destinations.** Unit
tests mocked these polls; a new test runs all three against the real schema.

## The flaky full-stack test, explained
`test_6_a_missed_run_is_detected…` failed intermittently (R53 once, R63 twice). Logs from a failing run
showed no errors; the cause is the test's budget. A run binds to the nearest open slot up to 90 s
**before** it is due (`processor.attach_slot`), so a heartbeat can fill the next minute's slot and the
first slot that can go unfilled is up to two minutes away. Worst case ≈ 120 s + a 30 s reconciler
interval + tick time; the test allowed 150 s on the assumption of one minute. Now 240 s, with the
reasoning in the test. Product behaviour unchanged (early binding tolerates clock skew).

## Verification
- 8 unit tests (recurrence incl. daylight saving and the repeated hour, scope, validation) and 7
  integration tests: **a weekly window created four weeks ago still skips this week's run** (the bug),
  scoped windows skip only their jobs, the engine suppresses inside a recurring window, API
  create/list/end/delete with audit, status pages show only relevant windows, and creating a
  window that already ended (a 500 found by the router smoke test, fixed). Mutation-checked: with
  recurrence ignored again, the regression and engine tests fail.
- 2 tests run the exporter's polls against the real schema.
- Backend 817 passed / 0 failed; Playwright 88 passed / 0 failed; full stack 6/6 on three consecutive
  runs (180–212 s each), outbound-exporter log clean.

---

# R62 — the agent installer is served by JobWatch: one real install command

Closes the gap found while writing the docs (R59): the app showed
`curl -fsSL https://<server>/install.sh | … --server https://<ingest> …`, a placeholder command, and the
installer's download location defaulted to `downloads.example.com`.

## What ships
- **`GET https://<ingest>/agent/install.sh`** (`cronsentinel/agentinstall.py`): the repository's
  `install.sh` with its three default lines filled in: `AGENT_DOWNLOAD_BASE`, `AGENT_VERSION` and
  `INGEST_PUBLIC_URL`. Served from the ingest host because every server that runs the agent must reach
  it anyway. `Cache-Control: no-store`.
- **The app's command is real:** `curl -fsSL https://<ingest>/agent/install.sh | sudo bash -s -- --token csb_…`
  (no `--server`: the served script defaults to this deployment's ingest host). When it cannot be
  served, Servers & agents shows the token, the reason, and a link to the release-install fallback.
- **`install.sh`** has a marked defaults block (`DEFAULT_SERVER`, `DEFAULT_VERSION`, `DEFAULT_BASE`) and
  accepts `CS_SERVER`; run from a release it behaves as before. Checksum verification is unchanged.
- **Safety:** the script runs as root, so values are validated before they are written into it (plain
  `https://` URLs; a tag-like version). Anything else, or unset values, → **503** and no script.
- **Deploy:** the API image contains the installer (`AGENT_INSTALL_SCRIPT`); `deploy.yml` sets
  `AGENT_DOWNLOAD_BASE` to the repository's GitHub releases and `AGENT_VERSION` to the deployed tag,
  where the release job already uploads the binaries and `SHA256SUMS`; `deploy/smoke.sh` checks that the
  installer is served with this deployment's server. Helm values and `.env.example` list both settings.
- Docs: the Linux agent guide's install section is the one-line command (release install kept as a
  fallback note); the self-hosting guide documents the settings.

## Verification
- 9 unit tests: served script has the defaults and still verifies checksums, is valid shell
  (`bash -n`), refuses when unconfigured, and refuses five injection attempts (`$(…)` in the download
  URL, `http://`, `;` and `$(id)` in the version, backticks in the ingest URL); the install command; the
  repository script keeps its defaults block.
- **End to end, as root:** the served script against a fake HTTPS release server with its own
  certificate and a stubbed `systemctl`: downloaded from `<base>/<version>/`, both checksums verified,
  enrolled with `--server <ingest>` taken from the served defaults (no flag passed), service enabled.
  Then the binary was tampered with after `SHA256SUMS`: "checksum verification failed — not installing",
  no binary, link or unit installed, no enrollment.
- Backend 798 passed / 0 failed (a first run had 10 fixture errors while the database was recovering;
  the rerun and three repeats of the affected files were clean); Playwright 84 passed / 0 failed.

---

# R61 — /docs restyled after Datadog's documentation

Datadog's docs structure, adapted to JobWatch (its own design, not Datadog's branding).

## What ships
- **Guides | API reference tabs** in a docs header bar, as Datadog splits Docs and API.
- **"Use your JobWatch hosts" selector** (Datadog's site-selector idea): enter your ingest host (and
  optionally app host) once; every code sample on every docs page uses it instead of
  `ingest.example.com` / `<ingest>` / `app.example.com`. Remembered in this browser only; "Use the
  examples" resets.
- **Copy button** on every code block (with a screen-reader announcement).
- **Collapsible sidebar tree**: groups fold; the current page's group is open.
- **Breadcrumbs** (Docs / group / page) and **Further reading** at the end of each page (related
  pages set in the docs manifest).
- **Callouts** from GitHub-style Markdown (`> [!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`), used where
  readers need them: ping URLs work like passwords (rotation takes effect immediately; checked: the
  rotate endpoint clears the ingest cache), owners keep password sign-in under enforced SSO, timer
  schedules need agent R53+, and the Datadog integration was verified against a recording copy of
  its API, so check with a real key.
- **Search-first docs home**: a large search box and popular links.
- **`/llms.txt`**: a documentation index for AI agents (Datadog's docs publish one), generated at build
  time from the same manifest.

## Verification
- Playwright 84 passed / 0 failed; new tests cover tabs, breadcrumbs, callouts, further reading, copy
  buttons, the host selector rewriting samples across pages and resetting, and `/llms.txt`.
- axe-core colour contrast on six docs pages in light, dark and gold: 0 violations.
- All pages at 390 px in light and dark: no overflow or page errors.

---

# R60 — visual refresh: the "timetable" design system

JobWatch is about time slots and whether expected runs happened, so the palette and details borrow
from station departure boards and signage instead of generic SaaS teal-on-grey.

## What changed
- **Light (default):** navy ink `#14213D`, signage blue `#2346C8` for the primary action and the "now"
  marker, and a **navy signage sidebar** (tokens re-scoped inside `.app-sidebar`, so every child
  follows without per-element overrides).
- **Dark:** night navy `#0D1424` instead of near-black, periwinkle accent `#8EA6FF`.
- **Obsidian Gold:** the CSM Silks palette is unchanged; only the running colour was adjusted.
- **Status colours** retuned per theme; **running is violet** so it never reads as a link.
- **Type:** body 14 px (was 13.5), line-height 1.5; tabular numerals in tables, rows, durations and
  counts so times line up like a timetable. Same families (Space Grotesk, IBM Plex Sans/Mono).
- **Radius hierarchy:** controls 6 px, cards 10 px, overlays 14 px. Flat surfaces; shadow only for
  floating elements. Inputs get a soft focus ring; the primary button lost its glow.
- **Schedule tape:** one labelled "now" line (on the first row, inside the tape so the reveal
  animation does not clip it).
- **Removed decoration:** ALL-CAPS tracked labels (docs sidebar, docs index, integrations grid, "On
  this page" are now sentence case), radial gradient washes behind the hero, the sign-in panel and
  the closing band, and the glow on the highlighted pricing card (now a 2 px accent border).
- **Landing screenshots** re-taken in the new design.

## Accessibility fixes found by the audit
- Disabled alert rules were faded to 50% opacity, making their text and even the active Enable
  button unreadable (2.2–3.2:1). They now show a "Disabled" tag at full contrast.
- Comparison-table notes (`text-mute/80`, 3.9:1) and the "No" icon (`text-mute/60`) now use full
  `text-mute`.
- The `⌘K` badge rendered white on the navy sidebar (the page-background token was not re-scoped).

## Verification
- Every text token checked numerically against WCAG 4.5:1 on canvas, panel and raised surfaces in all
  three themes (lowest: light ok-green 5.4:1 on white).
- axe-core colour-contrast audit of 10 rendered pages (app, landing, sign-in, sign-up, docs, compare)
  in light, dark and gold: **0 violations** after the fixes above.
- Playwright 78 passed / 0 failed; all 26 pages at 390 px in light and dark, no overflow or errors.
- Before/after screenshots of five pages in both themes are in `visual-refresh/` with the release.

---

# R59 — documentation site at /docs

## What ships
- **`/docs`** in the web app (Docs is in the site navigation and footer): an index, 14 guides in five
  groups, and an **API reference**. Sidebar with search (titles, summaries and section headings), an
  on-page table of contents, previous/next links; light, dark and gold themes; phone layout.
- **Single source of truth:** pages are rendered at **build time** from the repository's `docs/*.md`
  (`apps/web/lib/docs.ts`), so the docs ship with every deploy and the running container needs no
  files. Links between Markdown files become `/docs/...` links; tables scroll on phones; round labels
  such as "(R54)" are stripped from public titles. The web Dockerfile now copies `docs/` for the build.
- **API reference** generated from the same OpenAPI document the web client's types come from,
  grouped by area, with methods, paths, query parameters and body markers.
- **New guides:** Quick start, Pings and heartbeat API, Kubernetes, Import (Healthchecks.io, Cronitor),
  Single sign-on, Security overview, How JobWatch works; **Linux agent rewritten**.
- Published as-is: Alerts, Run checks, SCIM, Audit-log streaming, Datadog, Deploying.
- **Not published:** the development log, product design document, rename notes, the R19 load-test
  log, and the older `SECURITY.md` / `ARCHITECTURE.md` / `DEPLOYMENT.md` engineering notes (kept in
  the repository). `SECURITY.md` was out of date (it says API keys are argon2-hashed; they have been
  SHA-256 since R21, and its "open items" were fixed in R39), so the public security page was written
  fresh from the current code.

## Corrections made while writing the guides
- The old `AGENT.md` said the agent had never been compiled or tested (it has been, since R39) and
  described key hashing and the passive mode inaccurately; rewritten from the code.
- **`install.sh` is not served by JobWatch** (the app shows it with a placeholder host). The release
  now ships `install.sh` next to the agent binaries and `SHA256SUMS`, and the Linux agent guide gives
  a working command against your GitHub release (`CS_DOWNLOAD_BASE` / `CS_AGENT_VERSION`).
- Claims checked against code before publishing: UI labels (Install an agent / Get install command),
  the Kubernetes agent does not report a time zone, CSRF applies to every cookie-authenticated request.

## Verification
- Production build generates every docs page statically.
- Playwright 78 passed / 0 failed, including new docs tests: every page listed, **every internal docs
  link answers 200**, headings/code/tables/table of contents render, round labels stripped, search
  finds a page by its section heading, API reference lists endpoints.
- All 26 pages at 390 px in light and dark: no horizontal overflow or page errors (the pings page
  overflowed on long inline URLs; fixed by letting inline code wrap).

---

# R58 — Datadog: alerts as events, audit log to Datadog Logs, job metrics

## Upgrade notes
- **Migration 0023**: `metric_exports` (row-level security), audit stream kind `datadog_logs`, plan
  features `datadog` and `metrics_export` on Team and Business (Enterprise has everything).
- No new service: metrics export runs in the existing outbound-exporter worker.

## What ships
- **Shared client** `cronsentinel/datadog.py`: Datadog's nine sites (US1, US3, US5, EU1, AP1, AP2,
  UK1, US1-FED, US2-FED, checked against Datadog's sites page); calls only `api.<site>` and
  `http-intake.logs.<site>`, never a tenant-supplied URL; `DD-API-KEY` auth; key format validated.
- **Alert channel `datadog`** (Events API): alert subject as title, alert details as text, alert_type
  error/warning/success, tags for job, host, env, team, severity and condition; one aggregation key
  per job so a failure and its recovery group together.
- **Audit stream `datadog_logs`** (Logs intake), with the same in-order, at-least-once delivery.
- **Job metrics export** (`cronsentinel/metricsexport.py`, Integrations → Datadog metrics):
  `jobwatch.run.duration`, `jobwatch.run.completed`, `jobwatch.run.missed`, `jobwatch.job.failing`,
  `jobwatch.job.reliability`, every minute, tagged job/env/team/host plus custom tags. A failed send
  keeps the window (backoff), so an outage delays metrics instead of losing them; catch-up in 6-hour
  slices; "Check API key" uses Datadog's validate endpoint.
- **Landing page:** an integrations grid (29 integrations, names only; notes that some depend on the
  plan). The Integrations page text no longer claims signal destinations are the only way data
  leaves JobWatch.
- `docs/DATADOG.md` covers setup, tags, metrics and an example monitor.

## Verification
- 6 integration tests against a recording mock of Datadog's API: alert event through the real
  notifier (host per site, path, key header, alert type, tags, aggregation key, rich title);
  audit log to the EU logs intake; metrics series names, values and tags with the window advancing;
  an auth failure keeping the window (mutation-checked: advancing it anyway fails the test); access
  and plan rules. Not run against a live Datadog account: use the in-app checks with a real key.
- Backend 789 passed / 0 failed; Playwright 70 passed / 0 failed.

---

# R57 — stream the audit log to a SIEM

The gap `/compare` named after R56: the audit log was only in the app and in exports.

## Upgrade notes
- **Migration 0022** adds `audit_streams` (row-level security) and the `audit_stream` plan feature on
  Business (Enterprise already has everything).
- No new service: delivery runs inside the existing **outbound-exporter** worker.

## What ships
- **Destinations** (owners only, Settings → Audit log streaming):
  - **Signed webhook**: `{"source":"jobwatch","type":"audit","events":[…]}`, signed with
    `X-JobWatch-Signature: sha256=HMAC(secret, body)`, batch ids in `X-JobWatch-Delivery`.
  - **Splunk HTTP Event Collector**: newline-joined HEC events, `Authorization: Splunk <token>`,
    optional index, `sourcetype=jobwatch:audit`, event time preserved.
- **Delivery** (`cronsentinel/auditstream.py`): every 5 s, up to 500 events per request, oldest first;
  each destination keeps a cursor, so it is **in order and at least once**. A failure leaves the
  cursor in place and backs off (10 s doubling to 15 min); the error is shown with the next retry.
  Several exporters never double-send (each stream row is locked while being worked).
- Start from new events only, or include the last 30 days. HTTPS only; internal addresses refused by
  the SSRF guard; TLS verified. Tokens and secrets are encrypted and never returned by the API.
- **Send test event** reports success or the destination's error without moving the cursor.
- Creating, pausing, resuming and removing a stream are themselves audited (and streamed).
- `docs/AUDIT_STREAMING.md` documents the event format and how to verify signatures.
- `/compare`: the audit-log row notes SIEM streaming; the "behind" list no longer names it.

## Verification
- 6 integration tests against a real local HTTP receiver: in-order signed delivery and cursor
  advance; an outage (HTTP 503) keeps events, backs off, then delivers everything in order; Splunk
  HEC format and auth header; only the org's own events plus 30-day backfill; two concurrent
  exporters deliver once; owner-only, plan gate, https/private-address refusal, test endpoint.
  Mutation-checked: without the row lock both exporters send the same batch.
- Backend 769 passed / 0 failed; Playwright 70 passed / 0 failed.

---

# R56 — SCIM groups: directory groups map to JobWatch roles

Item 4 of the research report (enterprise identity): SCIM covered people only, so everyone a
directory provisioned got one default role. Groups now sync, and owners map groups to roles.

## Upgrade notes
- **Migration 0021** adds `scim_groups`, `scim_group_members` (row-level security like every tenant
  table) and `scim_users.role_from_group`.
- In your directory's JobWatch app, enable group push. No JobWatch configuration beyond mapping roles.

## What ships
- **SCIM `/Groups`** (was an empty stub): list (filters `displayName eq`, `externalId eq`,
  `excludedAttributes=members`), get, create, replace, patch, delete; `ResourceTypes` and `Schemas`
  now describe Group. Both PATCH dialects: Okta (`path: members`, `members[value eq "…"]`, `replace`
  with a value object) and Entra ID (capitalised ops, `Remove` with a value list, `path: displayName`).
  Members must be people provisioned to the org; unknown ids are ignored. Duplicate names → 409.
- **Group → role mapping** (Settings → Single sign-on → Groups and roles; `PUT
  /api/v1/sso/scim-groups/{id}/role`, **owner-only** because a mapping can grant admin): viewer to
  admin, never owner. Rules:
  - the highest role among a person's mapped groups wins; unmapped groups change nothing;
  - **owners are never changed**;
  - deactivated people keep no access; reactivated, they return with their groups' role;
  - leaving every mapped group reverts a group-given role to the default role; a role set by hand
    for someone in no mapped group is left alone;
  - recomputed on every membership change, group deletion and mapping change.
- **Audit:** every resulting role change is logged as `scim.role_sync` with from/to and the actor
  (the directory's SCIM token or the owner who changed the mapping).
- `/compare`: directory sync now says users and groups; the "behind" list names audit log streaming
  to a SIEM as the remaining gap. `docs/SCIM.md` explains setup and rules.

## Verification
- 6 new integration tests: Okta push and mapping, Entra dialect with highest-role-wins and delete
  reverting, owners and deactivated people untouched (and reactivation restoring the group role),
  hand-set roles left alone, owner-only mapping and no owner role, unknown members and duplicates,
  and audit entries with real actors. Mutation-checked: without the owner guard the owner test fails.
  The first run caught an audit write with no actor for owner-made mappings; fixed.
- Backend 745 passed / 0 failed; Playwright 70 passed / 0 failed.
- As with users, not yet run against live Okta or Entra ID: run each vendor's SCIM validator on staging.

---

# R55 — alerts say which server, which job, which environment, and why

Alert subjects were `✕ [HIGH] nightly-db-backup: failed`; the person paged had to open the dashboard
to learn where it ran and what went wrong. Every alert now carries that context.

## What ships
- **Subject:** `✕ [HIGH] FAILED · nightly-db-backup on prod-db-01 (production) — exit 2 after 4m 12s`.
  Where = the run's host or the job's server, or `in <cluster>/<namespace>` for Kubernetes; the reason
  is the broken run check, "no run started for <slot in the job's time zone>", "still running after …",
  "exit N after …" or "succeeded in …". Capped at 180 characters.
- **Details** in every channel: job, status change, server or cluster/namespace/pod/node, environment,
  workspace, team, tags, schedule, expected slot (missed/late), started/finished/duration/exit code,
  failed run checks, metrics, last stderr line (redacted), incident, run id and a link to the job.
- **Per-channel layout:** Slack attachment fields, Teams facts, Discord embed fields, email/Telegram
  as labelled lines, PagerDuty `custom_details` + a link, Opsgenie `details`, and a `context` object
  in signed webhook payloads.
- One lookup per alert in the notifier (`cronsentinel/alerting/context.py`) from ids already in the
  payload, so rule alerts, auto-resolves and test sends all get it; if the lookup fails, the basic
  alert still goes out. Commands are deliberately excluded (credentials in arguments).
- `docs/ALERTS.md` shows the subjects and fields.

## Verification
- 5 new integration tests: subject and body from real DB rows (server, environment, team, tags,
  schedule in IST, duration, exit code, failed check, metrics, link), missed-run slot naming, the
  180-character cap and test sends, the real notifier task passing context to a channel, and the
  Slack/Teams/Discord structures. A `password=` in the stderr line is masked in the alert.
- Backend 726 passed / 0 failed; full-stack alert chain through NATS and Celery 6/6.

---

# R54 — run checks: a run that exits 0 but did the wrong thing is a failure

Item 3 of the next-round research report: the most-cited real-world failure is "the backup ran, exit
code 0, and wrote an empty file". Jobs can now carry run checks (assertions), and runs report metrics.

## Upgrade notes
- **Migration 0020** adds `executions.metrics` (JSONB) and `jobs.assertions` (text array).
- New Linux agent needed only for `$JOBWATCH_METRICS_FILE`; the heartbeat API and Cronitor paths work
  with any agent.

## What ships
- **Run checks** (`cronsentinel/assertions.py`): `metric.<name> <op> <number>` and
  `duration <op> <time>` (Cronitor's `metric.duration` spelling accepted). Up to 20 per job, validated
  on create/update (422 with the reason). A metric named in a rule but not reported fails the rule.
- **The processor evaluates them** when a run reports success: a broken rule turns the run into a
  failure, stores the reasons in `meta.assertion_failures` (e.g. `metric.size_bytes > 0 (was 0)`), and
  the job's state and alerts follow as for any failure. Failed runs are not re-checked.
- **Metrics in from every path:** heartbeat JSON `metrics`; Cronitor `metric=name:value` (now stored as
  real metrics instead of meta); agent events; and `cs-run`, which hands the job a private
  `$JOBWATCH_METRICS_FILE` and attaches `name=value` lines from it to the final event.
- **Cronitor import:** per-run assertions become run checks (enforced); `metric.duration < X` stays
  the expected runtime; aggregate assertions are reported, not imported.
- **Job page:** a "Run checks" panel to view and edit rules; a run shows its metrics, and a run that
  broke a check shows "Exited successfully but failed its run checks" with each rule and value.
- `GET /jobs/{id}/executions` returns `metrics` and `assertion_failures`; `docs/RUN_CHECKS.md` explains it.
- Landing page backup card now mentions the size check.

## Verification
- Backend 721 passed / 0 failed (new: 16 grammar/evaluation unit tests; 6 integration tests covering
  the empty backup, a script that stopped reporting, measured duration from start/finish, failed runs
  not re-checked, agent-event metrics, API validation and clearing; Cronitor import now asserts the
  imported rule is enforced). Mutation-checked: with evaluation disabled the empty-backup test fails.
- Agent: `go vet` and tests, including a real `cs-run` run whose script writes the metrics file.
- Full stack (NATS, workers): alert chain 6/6. Playwright 70 passed / 0 failed (new run-checks test).

---

# R53 — systemd timers get real schedules, so a timer that stops is a missed run

Closes the gap found while writing the R52 landing page: the agent discovered systemd timers, but
their schedules were not cron, so they had no expected runs and silence went unnoticed.

## What ships
- **`cronsentinel/oncalendar.py`** translates systemd schedules to cron:
  - the agent's `TimersCalendar` property (`{ OnCalendar=Mon..Fri *-*-* 09:00:00 ; next_elapse=… }`,
    already normalized by systemd), raw expressions (`daily`, `Mon..Fri 09:00`, `*:0/15`,
    `*-*-01 03:00 UTC`) and shorthands (minutely … annually, quarterly, semiannually);
  - weekday lists and ranges, `*`, lists, `a..b` ranges, `a/n` and `*/n` steps, a trailing time zone;
  - interval timers (`OnUnitActiveSec=15min`) become an every-period cron with the period added to grace.
- **Refused, with a reason, never guessed:** a specific year, non-zero seconds, `~` (from month end),
  weekday and day-of-month both set (systemd needs both, cron fires on either), several differing
  OnCalendar entries, weekday ranges that wrap (systemd rejects those too), one-shot boot timers.
- **Random delays:** the agent now reports each timer's `RandomizedDelaySec + AccuracySec`
  (`timer_slack_s`); the server adds it to grace. certbot's 12-hour delay no longer looks late.
  **Agents older than R53 do not report it, so their timers stay unscheduled as before** — upgrade the
  agent to get timer schedules.
- **Discovery rules:** new timer jobs get schedule, time zone and grace. An existing unscheduled timer
  gets its schedule filled in with slot generation starting now (no backlog of "missed" runs from
  before the schedule was known). A schedule set by a person is never overwritten by a translated one.
  Native crontab lines still refresh as before.
- **Discovery time-zone fix:** discovered jobs were inserted without a time zone and their next run
  computed in UTC; both now use the schedule's zone.
- **Healthchecks import:** OnCalendar checks are converted instead of skipped (live import updated).
- Landing page: the certificate-renewal use case now states what the product does.

## Verification
- **Against systemd itself:** for 22 expressions, `systemd-analyze calendar` and the translated cron
  give the same next 15 run times from the same base time, including Asia/Kolkata and
  America/New_York zones and certbot's `*-*-* 00,12:00:00`. These tests skip where systemd-analyze is
  not installed (GitHub's Ubuntu runners have it).
- 8 discovery integration tests; mutation-checked: removing "slots start now" or "never overwrite a
  person's schedule" each makes a test fail.
- Agent: `go vet` and tests including the new timespan parser.
- Backend 699 passed / 0 failed; Healthchecks live tests; Playwright 68/0.
- **Flaky test fixed:** `test_overdue_slots_become_missed_and_job_failing` asserted a "failing" change
  even when the job was legitimately "late", so it failed in the ~90 s after every 5-minute boundary.
  Verified passing across a whole cycle.
- **Full stack:** one run had `test_6_a_missed_run_is_detected_and_alerted_with_no_heartbeat_at_all`
  time out (reconciler did not flag the job within 150 s). The next three runs passed 6/6; run times
  on this single shared CPU varied from 86 s to 208 s, and R53 does not touch the reconciler,
  scheduler or rule engine. Treated as CPU starvation in the sandbox; watch it in CI.

---

# R52 — more incident.io-style sections on the landing page

Added the incident.io homepage sections JobWatch can fill honestly; skipped the ones it cannot
(customer logos, testimonials, G2 badges, event promos), because JobWatch has none of those yet.

- **"Where you work" layer** at the top of the platform diagram (web app, Slack and Teams, email,
  webhooks and API, status pages), after incident.io's interfaces row.
- **Lead-in above the product tour** ("From 'did it run?' to 'it's handled'").
- **AI copilot section** (incident.io's "built on Nexus" slot): what the copilot reads, that secrets
  are redacted first, that it never runs commands, and that it can use a self-hosted model, next to
  a real screenshot of a copilot answer, labelled as demo data.
- **"Built for the jobs that fail quietly"** — six use-case cards (backups, ETL, billing, Kubernetes
  CronJobs, certificate renewals, nightly CI) in place of a customer-story grid: what goes wrong, and
  what JobWatch does about it.
- **Closing band:** full-width dark call to action replacing the plain closing section.

## Claim corrected before shipping
The certificate-renewal card first said a skipped systemd timer "is a missed run". It is not, yet:
the agent discovers timers, but their `OnCalendar` schedules are not cron, so discovered timers have
no expected schedule until one is set. The card now says so. **Product gap worth a round:**
translate common `OnCalendar` forms (daily, weekly, `*-*-* HH:MM`) to cron at discovery.

## Verification
Production build; Playwright 68 passed / 0 failed; all 22 pages at 390 px in light and dark, no
overflow or page errors; landing reviewed in light, dark and phone captures. No backend changes.

---

# R51 — landing page, sign-up and sign-in rebuilt (layout inspired by incident.io)

## Landing page (`/welcome`)
Structure borrowed from incident.io's homepage; words, visuals and claims are JobWatch's own.
- Announcement bar for the migration path (Healthchecks.io and Cronitor import).
- Centered hero with a **real product screenshot** (the overview page rendered from the demo data in
  `tools/ui-review`) and the schedule tape overlapping it.
- "Works with" strip (names only, no third-party logos).
- **Without / With JobWatch** toggle: the same nine-day silent backup failure, caught in 15 minutes.
- **Platform layers:** where jobs run → detection → response → foundation.
- **Product tour** with tabbed real screenshots: Overview / Jobs / Run detail; Alerting / Incidents;
  Status page. Images are WebP, ~310 KB in total, statically imported (no `public/` dir needed in
  the standalone image) and lazily loaded below the fold.
- Kept: three ways to connect, security controls, comparison summary, pricing, FAQ (now mentions
  Cronitor), final call to action. Removed the older "how a run becomes an alert" section, which the
  before/after block replaces. Every "Start" button now goes to `/signup`.
- No customer logos, testimonials or ratings: JobWatch has none yet, so none are shown.

## Sign-up (`/signup`) and sign-in (`/login`)
- Split layout: form on the left; on wide screens a panel showing the product (schedule tape and
  three concrete points) instead of testimonials. Light, dark and gold themes; phone layout checked.
- **Sign-in:** configured social providers, then a work email. The email is checked with
  `/auth/discover`: a company with verified SSO goes straight to its identity provider; anyone else
  goes to Keycloak's password page with the email filled in (where password reset also lives).
- **Sign-up:** configured social providers (`/auth/signup?idp=…`, the provider creates the account
  on first use), or a work email: SSO domains join their company's workspace, everyone else gets
  Keycloak's registration form with the email filled in. New accounts land on `/onboarding`.
- **API:** `GET /auth/providers` lists the buttons to show, from the new `AUTH_SOCIAL_PROVIDERS`
  setting (empty by default; the dev compose file enables google, github, microsoft, which exist in
  its realm). `/auth/login` and `/auth/signup` accept only configured provider aliases or an
  organization's own IdP as hints.
- Credentials are never handled by these pages; all of that stays on Keycloak.

## Verification
- Backend 640 passed / 0 failed (new tests: provider list and hint allow-list, sign-up goes to
  Keycloak's registration endpoint and lands on onboarding). Ruff clean.
- Playwright **68 passed / 0 failed**, including 4 new auth-routing tests on desktop and phone
  (provider links, SSO discovery route, password-step route with `next`, sign-up route).
- All 22 pages checked at 390 px in light and dark: no horizontal overflow, no page errors.

## Not done
- Keycloak's own login and registration screens still use Keycloak's default theme; a matching
  Keycloak theme is the natural follow-up so the hand-off looks seamless.
- A sales contact address is not invented: set `NEXT_PUBLIC_SALES_EMAIL` to enable "Talk to us".

---

# R50 — import from Cronitor, with cronitor.link URLs that keep working

First item of the next-round research report (Cronitor migration path).

## What ships
- **Jobs → Import** now offers Healthchecks.io and Cronitor. Cronitor needs an API key with the
  `monitor:read` scope; optionally the **telemetry key** used in your `cronitor.link` URLs. Neither is
  stored: the API key is used for the request, the telemetry key survives only inside each job's
  hashed ping token.
- **Cronitor API client** (`cronsentinel/importers/cronitor.py`): HTTP Basic with the key as username,
  `page`/`pageSize` paging until a short page, `Cronitor-Version: 2025-11-28` pinned (lists every
  schedule; the older single `schedule` field is accepted too), and any of three response envelopes
  (bare list, `monitors`, `data`), because Cronitor's docs do not show the list envelope.
- **Mapping:** `job` and `heartbeat` monitors import (`check`/`site` are HTTP uptime and are
  reported); cron kept with timezone; `every N minutes|hours|days` converted when the period fits the
  clock, otherwise imported without a schedule and flagged; `at HH:MM` → daily cron; multiple
  schedules → first imported, the rest reported; `grace_seconds` with Cronitor's defaults (60 for jobs,
  0 for heartbeats); `metric.duration < X` → expected runtime; other assertions reported (enforcing
  them is the run-metrics item planned next); note, tags, paused carried over.
- **Cronitor-compatible telemetry on ingest:** `/cronitor/p/<key>/<monitor>` and the legacy
  `/cronitor/ping/<key>/<monitor>`, GET/POST/HEAD, with `state=run|complete|fail|tick|ok`,
  `series` pairing start and end into one run, `metric=duration:<s>` → run duration,
  other `metric=` values kept on the run, `message`, `status_code`, `host`, `env`.
  Migrating a script is `cronitor.link/` → `<ingest>/cronitor/`. Unknown monitors get 404
  (Cronitor would auto-create one; JobWatch does not, on purpose).
- The import router was refactored so both importers share one code path (limits, de-duplication,
  token reuse, cache invalidation); the Healthchecks live tests passed unchanged afterwards.
- Ping tokens may now be up to 200 characters (telemetry key + monitor key).

## How it was tested — read this
Cronitor is closed source, so unlike Healthchecks it could not be run locally. Tests run against
`tests/fakes/cronitor.py`, a stand-in written to Cronitor's public Monitor and Telemetry API docs
(Basic auth, scopes, paging, version header behaviour, 101 monitors to force a second page, every
envelope shape). **Before announcing the importer, run one import against a real Cronitor account**
— the list envelope and any undocumented fields are the most likely surprises; if they differ,
update the fake first so the tests describe reality.

## Verification
- 9 new tests (mapping, schedule translation, paging across all envelopes, key/scope errors, full
  import → ping with Cronitor paths → runs recorded with duration and exit code, re-run creates
  nothing, no-telemetry-key path, SSRF guard). Mutation-checked: without the start/end sequence
  rule the run's end is dropped and the test fails.
- Backend 637 passed / 0 failed; ruff clean. Web: typecheck, production build, Playwright 60/0.
- Not built: the Sentry check-in shim from the report (Sentry's REST response schema could not be
  verified); next on the list is run metrics and assertions.

---

# R49 — deploy pipeline, and three deployment bugs that only real domains expose

## Deploy pipeline (`.github/workflows/deploy.yml`, `docs/DEPLOY.md`)
- A version tag (or a manual run per environment) builds the API, web and K8s-agent images to GHCR,
  deploys with `helm upgrade --install --atomic` (migrations first, as the chart's pre-upgrade hook),
  runs `deploy/smoke.sh` against the live URLs, and rolls the application back if the smoke test fails.
- GitHub Environments carry the per-environment URLs and secrets; production can require a reviewer.
  Secrets reach Helm through a values file on the runner, never the command line.
- Tags also publish the Linux agent binaries and `SHA256SUMS` to the GitHub release (what `install.sh` downloads).
- `deploy/environments/{staging,production}.yaml`: non-secret per-environment settings.
- `deploy/smoke.sh`: API reachable, API talks to the database, SCIM routed to the API, web app up,
  ingest healthy, ingest serves pings and Healthchecks paths, and (with `SMOKE_PING_TOKEN`) a real ping
  accepted end to end.
- Linted with actionlint 1.7.7 and shellcheck (clean); every shell script in `deploy/` and `tools/`
  is shellcheck-clean at warning level.

## Bugs fixed (all invisible with local ports)
- **Every ping URL shown in the UI pointed at the wrong host.** The web app derived the ingest URL
  by replacing port 8000 with 8010 in the API URL. With real domains that is a no-op, so the job page
  and onboarding showed ping URLs on the app domain, which does not serve pings: every copied URL
  would 404. New `NEXT_PUBLIC_INGEST_URL` (build arg, set by the deploy workflow); the port swap
  remains only as the local fallback. API side: `INGEST_PUBLIC_URL` set by Helm.
- **SCIM was unreachable in Kubernetes.** The Ingress routed only `/api`, `/auth` and `/public` to
  the API; `/scim/v2` went to the web app and returned 404, so every directory sync would fail.
- **Same-host pings would be throttled at the edge.** One Ingress applied `limit-rps: 50` per client
  IP to both hosts; a server running many cron jobs pings from one IP and bursts at the top of the
  hour (the R47 bug, one layer up). The chart now has two Ingresses: app host 50 rps per IP, ingest
  host 1000 (`ingress.appLimitRps` / `ingress.ingestLimitRps`), with per-job limits enforced in the app.

## Verified
- Chart renders with defaults, staging and production values (both Ingresses checked: `/scim` → API,
  separate limits, separate TLS secrets).
- **Emulated deployment:** full stack + production web build with separate app and ingest URLs behind
  a proxy that routes exactly like the new Ingresses: smoke test 8/8, including a real ping end to
  end through the ingest host; the ingest URL is compiled into 25 page bundles.
- Backend 625 passed / 0 failed; Playwright 60 passed / 0 failed.

## Rollback note
The workflow rolls back the application, not the database. Migration 0016 (hashed tokens) is not
backward compatible with releases before R41; take a database backup before deploying a release
whose changelog lists a migration that drops or renames something (`docs/DEPLOY.md`).

## Not done here (needs your accounts)
Pushing to your repository, creating the GitHub Environments and secrets, and the first real deploy.
Steps are in `docs/DEPLOY.md`.

---

# R48 — import from Healthchecks.io, with ping URLs that keep working

## What ships
- **Jobs → Import from Healthchecks** (admins): paste a Healthchecks project API key (healthchecks.io
  or self-hosted), preview, then import. The key is used for the request only and never stored.
  The Healthchecks base URL goes through the SSRF guard.
- **Mapping** (`cronsentinel/importers/healthchecks.py`):
  - cron checks → same schedule, time zone and grace;
  - interval checks → a cron that fires every period when one exists (1 min … 1 day, 1 week), with
    grace = period + grace because interval checks keep their own phase; flagged "Schedule converted";
  - intervals with no cron equivalent (e.g. 90 min) → imported without a schedule, flagged "Needs a schedule";
  - systemd OnCalendar checks → reported, not imported (JobWatch schedules are cron);
  - paused, tags and description carried over; names already in the workspace are left alone, so
    re-running the import is safe; the plan's job limit is respected.
- **Ping URLs keep working.** With a read-write key, each job keeps its Healthchecks UUID as its
  ping token, and ingest now answers the Healthchecks ping protocol under `/hc/`:
  `/hc/<uuid>`, `/start`, `/fail`, `/<exit status 0-255>`, `/log`, `?rid=` run IDs, GET/POST/HEAD,
  POST body kept as output, plain-text `OK` responses. Migrating a script is
  `hc-ping.com/` → `<ingest>/hc/`. With a read-only key, jobs get new ping URLs, shown once.
- New setting `INGEST_PUBLIC_URL` (empty = API host) for the ping URLs the API reports.
- Landing FAQ and `/compare` mention the migration path.

## Verified against a real Healthchecks server
Healthchecks (open source, latest `main`) ran locally; checks were created through its own v3 API
(cron with time zone, hourly and 5-minute intervals, a 90-minute interval, an OnCalendar schedule).
The live tests (`HC_LIVE_URL`, `HC_LIVE_KEY`, `HC_LIVE_RO_KEY`) import them and then ping exactly
as a Healthchecks script would: start + exit 0 with the same `rid` pair into one run, `/log` is
accepted, `/fail` records a failure, unknown UUIDs get 404; a re-run creates nothing; a read-only
key issues working new URLs. Without a server the live tests skip; the mapping test always runs.

## Fixed while building it
- A ping sent to `/hc/<uuid>` **before** the import is cached as "unknown" for 60 s, which would
  have rejected that job's pings right after the import. The import now clears the cache for every
  token it assigns (mutation-checked: the live test fails without it).
- Test isolation: the integration fixture also clears the resolve cache between tests.

## Verification
- Backend: 626 passed, 0 failed; ruff clean. Web: typecheck, production build, Playwright 60/0.

---

# R47 — ingest under load: one real bug, one leak, one round trip saved

New `tools/loadtest/`: seeds a fresh org with N heartbeat jobs, drives `GET /ping/<token>` with a
stdlib-only Go generator against the full stack, waits for processing to drain, and checks that
every accepted ping became exactly one execution (exits non-zero otherwise).

## Bugs found and fixed
- **Valid pings were rejected with 429 (introduced in R39).** The guard that slows down token
  guessing (60 lookups per minute per IP) charged every cache miss, including valid tokens not yet
  cached. One server — or one NAT gateway — with more than ~60 jobs had pings refused every time the
  5-minute cache expired; in the first load run 96% of pings were refused. Now only lookups that
  find no job count. Regression test: 100 valid jobs from one host all accepted (60 of 100 on the
  old code), and guessing is still limited.
- **Plaintext ping tokens in Redis.** The per-ping rate-limit key was `rl:hb:<token>`; R41 hashed
  the resolve cache but missed this key. It now uses the token's hash. The R41 test now scans all
  Redis keys for the plaintext (it fails on the R46 code).

## Performance
- The resolve-cache read and the rate-limit script now share one pipelined Redis round trip
  (`ratelimit.check_pipelined`). Profiling showed the two sequential round trips dominating sampled
  time; throughput on this box did not change because the single ingest process is CPU-bound, but
  Redis traffic per ping is halved, which matters once Redis is on another host.
- Measured (one shared vCPU running generator + ingest + workers + NATS + Postgres + Redis, one
  ingest process): warm cache about 320 pings/s at 20 clients (p50 55 ms) and 380/s at 100 clients;
  cold cache (2,000 never-seen tokens) 90-160/s. Exact accounting on a fresh org: 3,822 accepted,
  3,822 recorded — no loss, no duplicates. For scale, 10,000 jobs pinging every minute is ~170/s.
  Scale by ingest replicas (Helm default 2).

## Tests
- Integration tests now clear per-IP rate-limit windows before each test; counters from one test
  (or the previous run, within 60 s) were throttling the next.
- New: per-ping rate limit still enforced after the pipelining change.
- Backend: 620 passed, 0 failed; ruff clean across `apps/api` and `tools/`.

---

# R46 — Kubernetes agent compiled and tested against the real Kubernetes libraries

Until now the K8s agent had only been type-checked against hand-written stand-ins for the
Kubernetes packages (their module hosts are unreachable from the build sandbox). This round fetched
client-go v0.31.3, api and apimachinery and every transitive module through their GitHub mirrors,
using redirects generated from client-go's own `go.mod`, in a scratch copy of the agent.

## Results
- `go build ./...` and `go vet ./...`: clean, first real compile. The source compiled is
  byte-identical to the repo (checked by hash).
- Existing tests pass (disk queue, outage/restart delivery).
- **New `internal/watcher/fakecluster_test.go`** runs the real watcher — real informers, real API
  types — against client-go's fake clientset and a recording ingest server:
  - discovery reports only in-scope CronJobs, with schedule and concurrency policy;
  - a successful Job sends start then success; a failed Job sends fail carrying Kubernetes' reason
    (`BackoffLimitExceeded`);
  - three later updates of a finished Job send nothing more;
  - deleting a Job frees its per-Job state.
- Mutation check: with the R39 duplicate-event fix disabled, the same Job's success is sent
  **4 times**; with it, once.
- CI now runs `go test ./...` for the K8s agent (it only built and vetted before).

## Not changed
- No `go.sum` is committed: hashes produced through the mirrors are keyed differently from the
  real module hosts, so CI/Docker keep running `go mod tidy` (as before). Commit the `go.sum` that
  a networked build produces to make the image build reproducible.
- Not run on a real cluster (no Docker/kind in the sandbox). The fake clientset exercises the same
  informer and type code; a kind-based smoke test in CI would close the remaining gap.

---

# R45 — SSO and SCIM verified against a real Keycloak

R43/R44 were tested against simulated servers. This round ran them against **Keycloak 26.0.7**, with
a second Keycloak realm acting as a customer's identity provider, through real browser sign-ins.

## Results (live)
8/8 checks pass with the new `tools/sso-e2e` runner:
- Keycloak Admin API accepts the OIDC and SAML identity-provider representations (create and update).
- OIDC: user routed by email domain to the customer IdP, signs in, lands in the org with the default role.
  The `idp` claim from the realm mapper arrives in real tokens.
- Returning user signs in after SSO was disabled and re-enabled (the bug below).
- An IdP user asserting an address outside the org's verified domains is refused at the callback.
- SCIM-provisioned user links to the same record on first SSO sign-in and keeps the role set in JobWatch;
  after an Entra-style SCIM deactivation, SSO sign-in is refused.
- SAML with signed assertions: attribute mappers supply email and name; user lands in the org.

## Bugs found and fixed
- **Disabling SSO locked users out after re-enabling.** Disabling deleted the identity provider in
  Keycloak, which deletes every user's federated link. On re-enable, returning SSO-only users hit
  Keycloak's "account already exists, confirm by signing in" page with no password to confirm with.
  Disabling now switches the provider off (`enabled: false`); it is deleted only when SSO is removed
  or the organization is deleted. Unit test asserts the router never deletes on disable; live check passes.
- **Realm export listed `openid` as a client scope** (Keycloak warned and ignored it) and omitted
  `basic`, which since Keycloak 25 carries `sub` in access tokens. Bearer-token auth needs `sub`.
  Default scopes are now `basic, email, profile`; verified the access token contains `sub` and the
  re-import is warning-free. Re-run `deploy/keycloak/import.sh` (or add `basic` to the client's
  default scopes) on existing Keycloaks.

## New tool: `tools/sso-e2e/`
`customer_realm.sh` builds a fake customer IdP realm (OIDC and SAML clients, test users including one
that asserts a foreign address) and clears leftovers; `run.py` runs the checks above and exits non-zero
on failure, cleaning up its orgs and IdPs. Use it on staging after Keycloak upgrades or realm changes.

## Verification
- Backend: 618 passed, 0 failed; ruff clean (including the new tool). No web changes this round.
- Still not covered: Okta, Entra ID or ADFS specifically. Keycloak-as-IdP exercises the same OIDC
  and SAML paths, but each vendor has quirks; run one real sign-in per vendor a customer uses.

---

# R44 — SCIM directory sync, light default theme, Obsidian Gold theme

## Upgrade notes
- **Migration 0019** adds `org_scim_tokens` and `scim_users` (row-level security like every tenant
  table). SCIM is part of the Business and Enterprise plans (same `sso` feature as single sign-on).
- Nothing to configure server-side. An owner creates a SCIM token in Settings → Single sign-on and
  pastes it, with the base URL `<API_PUBLIC_URL>/scim/v2`, into Okta / Entra ID / another directory.

## SCIM 2.0 (RFC 7643/7644), users only
- Endpoints: ServiceProviderConfig, ResourceTypes, Schemas, `GET/POST /Users`, `GET/PUT/PATCH/DELETE
  /Users/{id}`, filters `userName eq`, `externalId eq`, `emails eq`. Groups answer with an empty list
  (writes 501): roles are assigned in JobWatch. Errors use the SCIM error schema and
  `application/scim+json`. Kept out of the web API's OpenAPI document (it is an IdP-facing protocol).
- Handles both PATCH dialects: Okta (`{"op":"replace","value":{"active":false}}`) and Entra ID
  (`{"op":"Replace","path":"active","value":"False"}` with string booleans).
- **Deactivation removes access on the next request**: it deletes the org membership, so cookie
  sessions, Keycloak bearer tokens and SSO logins all lose the org; the role is remembered and
  restored on reactivation. Delete removes the person from this org only (people can be in several).
- **Same domain rule as SSO:** only addresses in the org's verified domains can be provisioned.
- **Owners are never changed by SCIM** (deactivate/delete → 403), so a bad directory sync cannot
  remove the people who can fix it. Tokens can only be created and revoked by owners, are stored
  hashed, shown once, rate-limited (600/min) and record their last use.
- **SSO no longer re-admits deactivated people:** just-in-time membership on SSO login is refused
  for anyone the directory deactivated.
- Provisioned people have no Keycloak identity yet; their first SSO sign-in links to the row by email.
- Settings → Single sign-on gains "4. Directory sync (SCIM)": base URL, token list with last use,
  create (shown once) and revoke. `/compare` now marks single sign-on as supported, adds a
  directory-sync row, and lists group sync as the remaining gap.

## Light is the default theme
- First-time visitors now get the light theme (set before first paint, no flash). Dark (the
  WeCrew/Finspot tokens) and Obsidian Gold remain one click away; the switch cycles
  light → dark → gold and the choice is remembered per browser.
- Anyone who had never touched the switch was on dark before and will now see light; anyone who
  explicitly picked dark keeps it. Verified in a fresh browser: first visit is light, three clicks
  cycle all themes, a reload keeps the choice.
- Without JavaScript the page falls back to the dark values in `:root` (unchanged).

## Obsidian Gold theme
- A third theme alongside light (default) and dark: the CSM Silks palette — obsidian surfaces, warm
  ivory text, gold accent — with Playfair Display on page headings (numbers and tables stay in the
  sans-serif for legibility). The theme switch cycles light → dark → gold.
- **Approximated:** the CSM Silks site blocks automated access, so the values come from the CSM
  Silks reference portal's design language. Replace the `.gold` block in `apps/web/app/globals.css`
  with the site's exact tokens when you have them.

## Verification
- Backend: 617 passed, 0 failed; ruff clean; migrations through 0019.
- New: `tests/integration/test_scim.py` (discovery and auth, Okta lifecycle with role restore, Entra
  string booleans and path ops, deactivation ends an existing session, unverified domain refused,
  owners protected, token revocation, plan gate) and an SSO case proving a deactivated person cannot
  rejoin through JIT.
- Web: typecheck, production build, Playwright 60 passed / 2 skipped / 0 failed.
- Not tested against live Okta or Entra ID provisioning. Run each directory's own SCIM validator
  against staging before customers rely on it.

---

# R43 — single sign-on (SAML and OIDC), brokered through Keycloak

## Upgrade notes
- **Migration 0018** adds `org_sso` and `org_domains` (row-level security like every tenant table)
  and puts `sso` back on the Business plan. Enterprise already has everything.
- **New Keycloak client.** Run `deploy/keycloak/import.sh` again: it adds the `jobwatch-admin`
  service-account client (identity-provider roles only) and an `idp` claim mapper on
  `cronsentinel-web`, and prints `KEYCLOAK_ADMIN_CLIENT_SECRET` — set it on the API. Without it
  everything else works; only configuring SSO returns 503. Docker Compose dev gets it automatically.
- **New dependency:** `dnspython` (DNS TXT domain verification).
- Sessions now carry the identity provider they came from. Existing sessions keep working; they
  count as password sessions, which matters only once an org turns enforcement on.

## What ships
- **Settings → Single sign-on** (admins): connect an OpenID Connect provider (issuer, client ID,
  secret; endpoints are read from its discovery document) or SAML 2.0 (entity ID, SSO URL, signing
  certificate). JobWatch creates the identity provider in Keycloak and shows the redirect/ACS URL to
  register. The client secret is stored encrypted and never returned.
- **Verified email domains:** add a domain, publish the TXT record, press Check DNS. Public mailbox
  domains (gmail.com etc.) are refused, and a domain can be verified by one organization only.
- **Sign in with SSO** on the login page: enter a work email, go straight to that company's IdP.
- **Just-in-time membership:** first SSO sign-in joins the org with the role the admin chose.
- **Require single sign-on** (owners): every member except owners must come through the IdP —
  enforced on new logins, org switching, existing cookie sessions and Keycloak bearer tokens.
  Owners keep password sign-in as a break-glass. API keys are unaffected. Disabling the IdP turns
  enforcement off with it so nobody is locked out.
- Deleting an organization removes its identity provider from Keycloak first.

## Security properties (each has a test)
- An org's IdP can only sign in addresses in **that org's verified domains**. Without this, any org
  admin could configure an IdP asserting someone else's email and take over their account.
  Mutation-checked: removing the domain join makes the test fail.
- Issuer, discovery endpoints and SAML SSO URL go through the SSRF guard before Keycloak (which runs
  inside your network) is told to call them; all must be https.
- The `idp` login parameter accepts only `org-<uuid>` aliases.
- The Keycloak admin client can manage identity providers and nothing else.

## Verification
- Backend: 587 passed, 0 failed; ruff clean; migrations 0017 ⇄ 0018 clean.
- New tests: `test_sso_admin.py` (config, secret never returned, public domains refused, DNS
  verification, one-org-per-domain, owner-only enforcement, enforcement drops when IdP disabled,
  SAML validation, Business-plan gate), `test_keycloak_admin.py` (create vs update, SAML mappers,
  PKCE and discovered endpoints), and four SSO cases in `test_auth_flow.py` (routing by domain, JIT
  role, foreign-domain refusal, enforcement with owner break-glass).
- Web: typecheck, production build, Playwright 60 passed / 2 skipped / 0 failed.
- **Not tested against a live Keycloak or a real Okta/Entra/ADFS.** Keycloak's Admin API and the
  broker flow were exercised against mocks built from Keycloak's documented representations. Before
  offering SSO to customers, run one end-to-end sign-in per protocol on staging.
- Not built: SCIM provisioning (deprovisioning still happens in JobWatch). Listed as a gap on `/compare`.

---

# R42 — PagerDuty and Opsgenie, resolved on recovery

## Upgrade notes
- **Migration 0017** adds `pagerduty` and `opsgenie` to the Team and Business plans (Enterprise
  already has everything). Downgrade removes them.
- No configuration needed on the JobWatch side. Customers create a channel with a PagerDuty
  Events API v2 integration key or an Opsgenie API integration key, and choose region `us` or `eu`.

## What ships
- **PagerDuty (Events API v2)** and **Opsgenie (Alert API v2)** channels
  (`cronsentinel/alerting/oncall.py`). Every alert for a job uses one stable key
  (`jobwatch-job-<job id>`), so repeated failures update one incident instead of paging again.
- **Auto-resolve on recovery.** The notifier records which on-call channels hold an open alert for a
  job; when the job recovers, the rule engine resolves/closes them, even if no "recovered" rule
  exists and even during a maintenance window. Nobody is left paged by a job that already recovered.
- Severity maps to PagerDuty severity (critical/error/warning/info) and Opsgenie priority (P1–P4).
  PagerDuty events can link back to the job page (`dashboard_url`). Opsgenie `responders` supported.
- **Test button** triggers and immediately resolves, under a key that cannot touch a real alert.
- Keys are validated when the channel is created (format and region), and stored encrypted like
  every other channel secret. Vendor hosts are fixed by us, never taken from tenant input.
- Alerting page offers both types; landing, product tour and `/compare` updated. The R35
  brand-review test now allows "PagerDuty" and "Opsgenie" on public pages, since they ship.

## Bug fixed (pre-existing since before R39)
- **Every recovery of a job with an open incident crashed the rule engine.** The incident
  auto-resolve SQL embedded the JSON literal `{"auto":true}`; SQLAlchemy's `text()` read `:true` as
  a bind parameter with no value and raised after the incident had been marked resolved, so the
  rest of the handler (including recovery notifications) never ran. Now a bound parameter.
  Mutation-checked: the new end-to-end test fails on the old code. A scan found no other instance.

## Verification
- Backend: 553 passed, 0 failed; ruff clean; migrations 0015 → 0017 → 0015 → 0017 clean.
- New tests: `tests/test_oncall.py` (payloads, regions, resolve keys, test-closes-itself, vendor
  errors trigger retry) and `tests/integration/test_oncall_e2e.py` (key validation, plan gating,
  failure → page → recovery → resolve with no recovered rule).
- Web: typecheck, production build, Playwright 60 passed / 2 skipped / 0 failed.
- Not tested against live PagerDuty or Opsgenie accounts (requests checked against a mock transport
  built from the vendors' documented formats). Send a test notification from each before relying on it.

---

# R41 — hashed ping tokens, dark design system, new landing and comparison pages

## Upgrade notes
- **Migration 0016 hashes every heartbeat token.** Existing ping URLs keep working (the hash of the
  old token is stored), but the plaintext is gone: the UI can no longer re-display an existing URL.
  Users who need it again choose **New ping URL** on the job page. Downgrading issues fresh tokens.
- **Agents report by fingerprint.** `/agent/v1/discovery` now returns an empty `tokens` map (kept
  for wire compatibility). Deployed agents already fall back to fingerprints; no agent upgrade needed.
- **Dark is the default theme** (WeCrew/Finspot tokens). Light is one click away and remembered per browser.

## Security
- Ping tokens are stored as SHA-256 + a 4-character hint (`cronsentinel/pingtokens.py`). The full
  token is returned only by job create and `rotate-token`; every read returns `heartbeat_token: null`.
  The ingest cache is keyed by the hash. A database dump, a backup or a viewer-role API key no longer
  exposes working ping URLs. Regression test checks the jobs row and Redis for the plaintext.

## Product and design
- **Design system:** Finspot dark tokens (`#0A0F13`→`#1E3038` surfaces, `#22D3EE` accent, desaturated
  status colours) as CSS variables with a matching light set; Space Grotesk for headings, IBM Plex for
  text and code, all bundled. Theme applied before first paint.
- **App shell:** sidebar grouped into Monitor / Infrastructure / Insights / Configure with icons,
  active marker, command palette, theme switch. Page headers with subtitles; panels and tables on the
  new surface tokens; overview status numbers link to filtered job lists; charts follow the theme.
- **Landing page (`/welcome`)** rebuilt around a schedule tape: five job types over 24 hours showing
  runs that happened, failed, ran late and never started, with a "now" line. Then: three ways to
  connect, how a missed run becomes an alert, security controls, comparison summary, pricing, FAQ.
- **New `/compare` page:** JobWatch vs Healthchecks.io, Cronitor, Better Stack, Sentry Crons and
  Uptime Kuma across detection, onboarding, enterprise operation and pricing shape; head-to-head
  filter; "Where JobWatch is behind" (SSO, on-call integrations, licence, track record) and "When to
  choose something else". Data in `apps/web/lib/compare.ts`.
- **Comparison verified 1 October 2026** against vendor pages, with a Sources list on `/compare`.
  Corrections made: Cronitor on-call integrations are Business-only (now partial); Cronitor SSO is a
  $5/user add-on on Business; Cronitor discovery covers crontab and Windows Task Scheduler, not
  systemd; Better Stack schedule cell explains the interval-vs-cron-syntax conflict in its own docs;
  price-shape row now gives actual rates. Healthchecks.io unlimited team members confirmed (Feb 2026).
  Still unconfirmed and marked partial: whether Cronitor's agent buffers events on disk.
- **Job page:** "New ping URL" shows the new URL once with a copy button; snippets show
  `<token ending abcd>` otherwise. Onboarding says the URL is shown once.
- **Product tour (`/product`)** uses the shared site header and footer; fixed a mobile overflow.
- Jobs filter tabs scroll on narrow screens; landing tape scrolls horizontally on phones.

## Verification
- Backend: 546 passed, 0 failed (Postgres 16, Redis, NATS); ruff clean; migrations 0015⇄0016 clean.
- Web: typecheck, 21 unit tests, production build; Playwright e2e (desktop + Pixel 7) against the
  production build: 60 passed, 2 skipped (unchanged pre-existing skips), 0 failed.
- All 21 pages checked at 390 px in both themes: no horizontal overflow, no page errors.

---

# R40 — follow-ups

- **Editing a job's schedule always failed with a 500** (pre-existing since before R39): the audit
  entry carried a `datetime` that `json.dumps` cannot encode. Fixed. *(mutation-checked against the original)*
- **Rescheduling left stale slots:** future slots of the old schedule stayed `pending`, later went
  `missed` and alerted, and the new schedule was not materialised for the horizon already covered.
  A change to `schedule_expr`, `tz`, `grace_s` or `expected_runtime_s` now drops untouched future
  slots and regenerates from now. *(mutation-checked)*
- **`PATCH /api/v1/jobs/{id}` can clear fields.** Only fields the client sends are applied; explicit
  `null` clears `description`, `schedule_expr`, `expected_runtime_s`, `sla_target` (and resets `tags`
  to `[]`), and is a 422 for required fields. Previously every `null` was silently ignored, so a job
  could not be turned back into a pure heartbeat job. *(mutation-checked)*
- **K8s agent disk queue:** events are written to `/var/lib/cronsentinel/events.jsonl` (the state
  volume; `CS_BUFFER_PATH` overrides) and removed only after the server accepts them, so they survive
  agent restarts and ingest outages. Capped at 12 MiB; beyond that the in-memory fallback (20 000
  events) is used. Tested: outage → restart → recovery delivers every event exactly once.
- **Web:** "Rotate ping token" button on the job page (uses the R39 endpoint, with a confirmation).

Verification: backend 545 passed / 0 failed; ruff clean; web typecheck + unit tests pass; K8s agent
`go vet` + tests pass against stub k8s.io packages (still no real k8s.io compile in this sandbox).

---

# R39 — code-level security and reliability pass

Baseline: the `wecrew-jobwatch.zip` snapshot of 2026-09-29 (R38). Every item below was found by
reading or running the code. Every backend fix has a regression test; where marked *(mutation-checked)*,
the test was also run against the original code and failed there.

## Upgrade notes — read before deploying

1. **`SECRET_ENCRYPTION_KEY` is now mandatory and must be real.** The API refuses to start if it is
   empty, a known placeholder (`change-me`, `change-me-32-bytes-base64`, …) or shorter than 32
   characters. Generate one with `openssl rand -base64 48`. Keep the value you already run with if
   it passes; changing it logs every user out and makes stored channel configs unreadable.
   `ALLOW_INSECURE_SECRET=true` exists for tests only.
2. **docker-compose** now reads the key from `.env` and fails fast without it; use
   `docker compose --env-file .env -f infra/docker/docker-compose.yml …` (README updated).
3. **Redis ≥ 6.2** is required (`GETDEL`). The compose/Helm defaults (Redis 7) already satisfy this.
4. **Linux agents:** create the spool directory on existing hosts so non-root crontabs are monitored:
   `install -d -m 1733 /var/lib/cronsentinel/spool`. New installs get it from `install.sh`.
   Release artifacts must now include `SHA256SUMS` (`make release` produces it).
5. **K8s agent Helm chart:** `persistence.enabled` defaults to `true` (16 MiB PVC) so the enrolled
   identity survives pod rescheduling. Pods now run as uid/gid 65532.
6. **Web image** uses Next.js standalone output; `npm start` now runs `node .next/standalone/server.js`
   (copy `.next/static` into `.next/standalone/.next/` first, as the Dockerfile and CI do).
7. **Logins:** sign-in now requires `email_verified=true` from the IdP, and a user whose email is
   already bound to a different IdP subject gets a 409 instead of being silently re-linked.

## Security

| Sev | Fix | Where |
|---|---|---|
| Critical | Root secret defaulted to `"change-me"` → forgeable session cookies for any user/org on any install that forgot to set it. Now refused at first use. | `config.py`, `keys.py` |
| Critical | Next.js 15.1.0 / React 19.0.0 carried the RSC "flight" RCE, middleware auth bypass and ~30 other advisories. Now Next 15.5.27, React 19.0.8, postcss 8.5.28; production `npm audit` clean. | `apps/web/package*.json` |
| High | Open redirect after login: `/auth/login?next=@evil.com` → `https://app@evil.com`. `safe_next()` keeps only same-site absolute paths. | `routers/auth.py` |
| High | Account takeover by email: unverified emails were accepted and `keycloak_sub` was overwritten on email match. Now `email_verified` required and re-linking refused. | `routers/auth.py` |
| High | SMTP SSRF: the email channel connected to any tenant-supplied host/port with no guard; STARTTLS did not verify certificates. Now `netguard.smtp()` (resolve → validate → connect to the validated address, verified TLS) plus a config-time check. Telegram tokens validated. | `netguard.py`, `alerting/tasks.py`, `routers/alerting.py` |
| High | Cross-tenant execution-id collision: ids are client-chosen but keyed globally, so org B's events were dropped as duplicates of org A's, and a `start` could UPDATE A's execution row. Contested ids are now scoped to the job. *(mutation-checked)* | `processor.py` |
| Medium | Bootstrap tokens were reusable under concurrency (GET … DELETE). Now `GETDEL`. *(mutation-checked)* | `routers/agents.py` |
| Medium | Any viewer could export member emails + API-key inventory. Export now requires admin. *(mutation-checked)* | `routers/gdpr.py` |
| Medium | API-key role unvalidated (`"superuser"` stored, 500s later) and not capped at the creator's role. *(mutation-checked)* | `routers/org.py`, `auth.py` |
| Medium | Bearer JWTs: algorithms not pinned; JWKS cached forever (key rotation broke auth until restart); error text leaked internals; bad `X-Org-Id` → 500. | `auth.py` |
| Medium | Job create/update accepted another org's workspace/environment/team ids (FK checks bypass RLS). *(mutation-checked)* | `routers/jobs.py` |
| Medium | Unknown heartbeat tokens hit the DB on every request before any rate limit. Now per-IP limited + negative cache. | `routers/heartbeat.py` |
| Medium | Heartbeat/agent payloads unbounded (tails, meta, discovery lists, ids). Now bounded. *(mutation-checked)* | `schemas.py`, `routers/agents.py` |
| Low | Logout was CSRF-able; legacy OAuth state without nonce accepted; API-key prefix collisions could reject valid keys. | `routers/auth.py`, `auth.py`, `apps/web` |
| Low | Containers ran as root; web image shipped dev dependencies; agent installer did not verify downloads. | `infra/docker/*`, `install.sh` |

## Reliability / correctness

- **URL-mode heartbeats never paired:** `/heartbeat/<t>/start` then `/success` produced two executions,
  the first stuck "running". Completions now pair with the job's open id-less start. *(mutation-checked)*
- **Timestamps:** naive, unparseable or absurd (`>7 d` past / `>1 h` future) `agent_ts` crashed the
  processor (NATS redelivered 10× then dropped the run). Now normalised; rejected values kept in meta. *(mutation-checked)*
- **Unknown event kinds** reaching the processor were recorded as failures. Now ignored. *(mutation-checked)*
- **Pause/delete/rotate** were ignored for up to 5 min (token cache). Cache now invalidated; new
  `POST /api/v1/jobs/{id}/rotate-token`. *(mutation-checked)*
- **Invalid time zones** → 500. Now 422/400. *(mutation-checked)*
- **Agent enroll** failed when the client address was not an IP (audit `inet` cast). *(mutation-checked)*
- **Linux agent buffer:** one torn or >1 MiB line stopped delivery permanently; compaction could race
  wrapper appends. Lines are now validated/skipped, and all buffer I/O holds `flock(2)`.
  *(corrupt/partial-line tests mutation-checked; the compaction race is too narrow to reproduce on demand)*
- **Linux agent, non-root crontabs:** wrappers could neither read the config nor write the buffer, so
  those jobs ran unmonitored. They now spool per-run files; the daemon imports them and re-derives the
  fingerprint from the file owner, so a user cannot report runs of another user's jobs.
- **K8s agent did not compile** (unused variable in `client.go`). Also: `Pod`/`Node` shared one JSON tag,
  so neither was ever sent; restarts re-enrolled with the spent bootstrap token and crash-looped;
  success/fail were re-sent on every update and resync; per-job maps grew forever; event `count` was
  sent as a Unicode character; the retry queue was unbounded.

## Tooling

- Ruff rules and major version pinned (an unpinned newer ruff failed CI's first step with 227 findings).
- CI migration check: `alembic downgrade 0001` (0001's downgrade raises by design; `downgrade base`
  could never pass).
- Integration-test isolation: platform heartbeats/gaps reset before every test — the cause of the
  R38 "wall-clock flake" in `test_slot_lifecycle` / `test_unknown_suppression`.
- Agent `Makefile release` writes `SHA256SUMS`.

## Verification (this sandbox)

- Backend: 542 passed / 37 skipped / 0 failed against Postgres 16, Redis 7, NATS JetStream
  (skips: full-stack tests needing `scripts/fullstack.sh up`, two external-service cases).
- `ruff check .` clean; alembic `downgrade 0001` → `upgrade head` clean.
- Linux agent: `go vet`, `go test` pass (Go 1.22 toolchain; module declares 1.23).
- K8s agent: `go vet` against stub k8s.io packages only — the real modules were unreachable here.
  CI (`go mod tidy && go build`) is the first real compile.
- Web: typecheck, 21 unit tests, `next build`, standalone server smoke test (8 routes 200).
  Playwright e2e not run (browser download unreachable here).
- Helm: both charts render with `tools/mini-helm`; agent chart checked with persistence on and off.
- Full stack (`scripts/fullstack.sh up` — api, ingest, exec-processor, schedule-generator, reconciler,
  rule-engine, outbound-exporter, notifier as real processes): `tests/fullstack/` 9 passed, including the
  Linux-agent end-to-end test against the patched agent binary.

## Not done

- Heartbeat tokens are still stored in plaintext (needed to display ping URLs).
