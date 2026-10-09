// Competitive comparison shared by /welcome (summary) and /compare (full). Checked against each
// vendor's public pricing and docs pages on 1 October 2026 (SOURCES below). Vendors change plans often: every cell is
// a claim we must be able to defend, so uncertain capabilities are "partial" with a note, never "yes".
// JobWatch cells must match what the code ships (see tests/e2e/pages.spec.ts brand review).
export type Cell = "yes" | "no" | "partial" | string;
export type Vendor = { key: string; name: string; note?: string };

export const VENDORS: Vendor[] = [
  { key: "jw", name: "JobWatch" },
  { key: "hc", name: "Healthchecks.io" },
  { key: "cr", name: "Cronitor" },
  { key: "bs", name: "Better Stack" },
  { key: "se", name: "Sentry Crons" },
  { key: "uk", name: "Uptime Kuma" },
];

export type Row = { label: string; help?: string; cells: Record<string, Cell>; note?: string };
export type Group = { title: string; rows: Row[]; summary?: boolean };

export const GROUPS: Group[] = [
  { title: "Detecting problems", rows: [
    { label: "Cron-expression schedules", help: "Knows when each run is due, not only how long since the last ping",
      cells: { jw: "yes", hc: "yes", cr: "yes", bs: "partial", se: "yes", uk: "no" }, note: "Better Stack's heartbeat docs describe an expected interval plus grace; its own comparison pages also claim cron syntax. Uptime Kuma push monitors are interval-only." },
    { label: "Every expected run tracked as its own record", help: "A missed 04:15 run is a row you can see, count and report on",
      cells: { jw: "yes", hc: "partial", cr: "partial", bs: "no", se: "partial", uk: "no" }, note: "Others alert on a missed run; JobWatch also keeps each slot for SLA and history." },
    { label: "Start, success and failure signals with exit code", cells: { jw: "yes", hc: "yes", cr: "yes", bs: "partial", se: "yes", uk: "no" } },
    { label: "Run output captured with the failure", cells: { jw: "yes", hc: "yes", cr: "yes", bs: "no", se: "partial", uk: "no" },
      note: "Healthchecks.io keeps the ping body; Sentry links errors rather than job output." },
    { label: "Platform outages never page you", help: "If the monitor itself was down, those slots are marked unobserved, not missed",
      cells: { jw: "yes", hc: "no", cr: "no", bs: "no", se: "no", uk: "no" }, note: "We could not find this documented elsewhere; tell us if we are wrong." },
  ], summary: true },
  { title: "Getting jobs in", rows: [
    { label: "Ping URL, no install", cells: { jw: "yes", hc: "yes", cr: "yes", bs: "yes", se: "yes", uk: "yes" } },
    { label: "Discovers Linux crontabs and systemd timers", cells: { jw: "yes", hc: "no", cr: "partial", bs: "no", se: "no", uk: "no" },
      note: "Cronitor's CLI reads crontabs and Windows Task Scheduler; its docs do not mention systemd timers." },
    { label: "Kubernetes CronJobs discovered by an in-cluster agent", cells: { jw: "yes", hc: "no", cr: "yes", bs: "no", se: "no", uk: "no" } },
    { label: "Agent keeps events on disk through outages", cells: { jw: "yes", hc: "no", cr: "partial", bs: "no", se: "no", uk: "no" },
      note: "Not described in Cronitor's public docs either way; marked partial until confirmed." },
  ], summary: true },
  { title: "Running it in an enterprise", rows: [
    { label: "Self-host in your own network", cells: { jw: "yes", hc: "yes", cr: "no", bs: "no", se: "yes", uk: "yes" } },
    { label: "Open-source licence", cells: { jw: "no", hc: "yes", cr: "no", bs: "no", se: "partial", uk: "yes" }, note: "Sentry is source-available (FSL), not OSI open source." },
    { label: "Multi-tenant isolation enforced by the database", cells: { jw: "yes", hc: "no", cr: "no", bs: "no", se: "no", uk: "no" },
      note: "Not publicly documented by the others; they may isolate tenants another way." },
    { label: "Role-based access", cells: { jw: "yes", hc: "yes", cr: "partial", bs: "yes", se: "yes", uk: "no" },
      note: "JobWatch: six roles. Healthchecks.io: owner, manager, user, read-only. Cronitor: admin, user and read-only on Business." },
    { label: "Audit log and full data export", cells: { jw: "yes", hc: "partial", cr: "partial", bs: "partial", se: "yes", uk: "no" },
      note: "JobWatch also streams the audit log to a SIEM (signed webhook or Splunk HEC) on Business and Enterprise." },
    { label: "Single sign-on (SAML or OIDC)", cells: { jw: "yes", hc: "no", cr: "partial", bs: "yes", se: "yes", uk: "no" },
      note: "JobWatch: SAML and OIDC with verified domains and enforcement, on Business and Enterprise. Cronitor: SAML on Business at $5 per user per month extra. Sentry: SAML on Business." },
    { label: "Directory sync (SCIM)", cells: { jw: "yes", hc: "no", cr: "partial", bs: "partial", se: "yes", uk: "no" },
      note: "JobWatch: users and groups, with directory groups mapped to roles (owners are never changed). Sentry: SCIM on Business. Cronitor and Better Stack: not confirmed in the public pages we checked." },
    { label: "PagerDuty and Opsgenie, resolved on recovery", cells: { jw: "yes", hc: "yes", cr: "partial", bs: "yes", se: "yes", uk: "yes" },
      note: "JobWatch closes the PagerDuty incident or Opsgenie alert when the job recovers. Cronitor: on-call integrations on Business, not the free plan. JobWatch and Better Stack also run on-call schedules and escalations themselves." },
  ] },
  { title: "Price shape", rows: [
    { label: "How you are charged", cells: { jw: "Per plan, unlimited users", hc: "Per plan, unlimited users", cr: "$2 per monitor plus $5 per user", bs: "$20 per extra 10 heartbeats, plus responders", se: "$0.78 per extra monitor", uk: "Free, you run it" } },
    { label: "Free tier", cells: { jw: "5 jobs", hc: "20 checks", cr: "5 monitors", bs: "10 heartbeats", se: "1 monitor", uk: "Unlimited" } },
  ] },
];

export const CHECKED_ON = "1 October 2026";
export const SOURCES: [string, string][] = [
  ["Healthchecks.io: unlimited team members on all plans (Feb 2026)", "https://blog.healthchecks.io/2026/02/unlimited-team-sizes-for-all/"],
  ["Cronitor pricing: free 5 monitors, $2/monitor + $5/user, SAML SSO, on-call integrations", "https://cronitor.io/pricing"],
  ["Cronitor Kubernetes agent", "https://github.com/cronitorio/cronitor-kubernetes"],
  ["Better Stack heartbeat monitor docs", "https://betterstack.com/docs/uptime/cron-and-heartbeat-monitor/"],
  ["Sentry Crons: one free monitor, $0.78 per extra", "https://sentry.zendesk.com/hc/en-us/articles/23058282687259-How-does-the-pricing-for-Crons-work"],
  ["Sentry plans: SAML + SCIM on Business", "https://sentry.io/pricing/"],
];
