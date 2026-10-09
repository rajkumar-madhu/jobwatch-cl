"use client";
/* R51 landing sections. Layout ideas taken from incident.io's homepage (big product view in the hero,
   before/after, a layered platform map, product areas with tabbed screenshots); the words and the
   screenshots are JobWatch's own — real app pages rendered from the demo data in tools/ui-review. */
import Image, { type StaticImageData } from "next/image";
import Link from "next/link";
import { useState } from "react";
import { Check } from "lucide-react";
import shotOverview from "./shots/overview.webp";
import shotJobs from "./shots/jobs.webp";
import shotJob from "./shots/job.webp";
import shotIncidents from "./shots/incidents.webp";
import shotAlerting from "./shots/alerting.webp";
import shotStatus from "./shots/status.webp";
import shotCopilot from "./shots/copilot.webp";

export function ProductFrame({ src, alt, priority = false, url = "app.jobwatch.example" }: { src: StaticImageData; alt: string; priority?: boolean; url?: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-[0_30px_80px_-40px_rgb(0_0_0/.45)]">
      <div className="flex items-center gap-2 border-b border-line bg-raised/60 px-3 py-2" aria-hidden>
        <span className="h-2.5 w-2.5 rounded-full bg-line" /><span className="h-2.5 w-2.5 rounded-full bg-line" /><span className="h-2.5 w-2.5 rounded-full bg-line" />
        <span className="ml-3 truncate rounded bg-bg px-2 py-0.5 font-mono text-[11px] text-mute">{url}</span>
      </div>
      <Image src={src} alt={alt} priority={priority} sizes="(min-width: 1024px) 1100px, 100vw" className="h-auto w-full" />
    </div>
  );
}

export function HeroProduct() {
  return <ProductFrame src={shotOverview} priority alt="JobWatch overview: jobs that need attention, run counts by state, the last seven days and open incidents" />;
}

const WORKS_WITH = ["Linux cron", "systemd timers", "Kubernetes CronJobs", "GitHub Actions", "GitLab CI", "Jenkins", "Airflow",
  "Slack", "Microsoft Teams", "PagerDuty", "Opsgenie", "Okta", "Microsoft Entra ID", "Healthchecks.io import", "Cronitor import"];

export function WorksWith() {
  return (
    <section className="border-b border-line" aria-label="Works with">
      <div className="mx-auto max-w-[1200px] px-6 py-10">
        <p className="text-center text-sm text-mute">Watches the schedulers you already run, and alerts where your team already works</p>
        <ul className="mt-5 flex flex-wrap justify-center gap-x-6 gap-y-3 text-[15px] font-medium text-ink/70">
          {WORKS_WITH.map((w) => <li key={w}>{w}</li>)}
        </ul>
      </div>
    </section>
  );
}

const BEFORE: [string, string][] = [
  ["Mon 02:00", "The nightly backup stops running after a credentials change. Cron logs nothing anyone reads."],
  ["Tue–Sun", "Every dashboard is green. Nothing failed, because nothing ran."],
  ["Day 9, 14:10", "A restore is needed. The newest backup is nine days old."],
];
const AFTER: [string, string][] = [
  ["Mon 02:00", "JobWatch already expects the 02:00 run, with a 15-minute grace window."],
  ["Mon 02:15", "No start signal: the run is marked missed and #ops on Slack is alerted. PagerDuty pages the on-call owner."],
  ["Mon 02:40", "The fix ships; the next run succeeds and the page resolves on its own."],
];

export function BeforeAfter() {
  const [tab, setTab] = useState<"before" | "after">("before");
  const rows = tab === "before" ? BEFORE : AFTER;
  return (
    <section className="border-b border-line">
      <div className="mx-auto grid max-w-[1200px] gap-12 px-6 py-20 lg:grid-cols-[1fr_1.1fr] lg:items-center">
        <div>
          <h2 className="font-display text-3xl font-semibold sm:text-4xl">The failure you don&apos;t see is the one that runs nothing</h2>
          <p className="mt-4 text-[17px] text-mute">Error tracking catches jobs that crash. It cannot see a job that never started: a deleted crontab line, a host that was replaced, a CronJob stuck behind a concurrency policy. JobWatch expects every run, so silence is a signal.</p>
        </div>
        <div className="card p-2">
          <div className="grid grid-cols-2 gap-1 rounded-lg bg-raised/60 p-1" role="tablist" aria-label="Compare">
            {(["before", "after"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={`rounded-md px-3 py-2 text-sm font-medium transition ${tab === t ? "bg-panel text-ink shadow-sm" : "text-mute hover:text-ink"}`}>
                {t === "before" ? "Without JobWatch" : "With JobWatch"}
              </button>))}
          </div>
          <ol className="space-y-0 p-4" role="tabpanel">
            {rows.map(([when, what], i) => (
              <li key={when} className="relative grid grid-cols-[96px_1fr] gap-4 pb-5 last:pb-0">
                <span className={`font-mono text-xs ${tab === "after" ? "text-accent" : "text-mute"}`}>{when}</span>
                <span className="text-sm">{what}</span>
                {i < rows.length - 1 && <span className="absolute left-[103px] top-5 h-[calc(100%-12px)] w-px bg-line" aria-hidden />}
              </li>))}
          </ol>
          <p className={`mx-4 mb-4 rounded-md px-3 py-2 text-sm font-medium ${tab === "before" ? "bg-bad/10 text-bad" : "bg-ok/10 text-ok"}`}>
            {tab === "before" ? "Found after nine days, by the person who needed the backup." : "Found after 15 minutes, by the system that expected the run."}
          </p>
        </div>
      </div>
    </section>
  );
}

const LAYERS: { name: string; items: string[] }[] = [
  { name: "Where you work", items: ["Web app", "Slack and Teams", "Email", "Webhooks and API", "Status pages"] },
  { name: "Where jobs run", items: ["Linux agent", "Kubernetes agent", "Ping URL", "CI pipelines", "Imports"] },
  { name: "Detection", items: ["Expected runs", "Late, missed, timed out", "Run output", "Outage-aware"] },
  { name: "Response", items: ["Alert rules", "Incidents", "PagerDuty and Opsgenie", "Status pages", "AI copilot"] },
  { name: "Foundation", items: ["SSO and SCIM", "Audit log", "Tenant isolation", "Self-host"] },
];

export function PlatformLayers() {
  return (
    <section className="border-b border-line bg-panel/40">
      <div className="mx-auto max-w-[1200px] px-6 py-20">
        <h2 className="max-w-[720px] font-display text-3xl font-semibold sm:text-4xl">One platform, from the first ping to the resolved page</h2>
        <div className="mt-10 space-y-3">
          {LAYERS.map((l, i) => (
            <div key={l.name} className="grid gap-3 rounded-xl border border-line bg-panel p-4 md:grid-cols-[180px_1fr] md:items-center">
              <div className="flex items-center gap-3">
                <span className="grid h-7 w-7 place-items-center rounded-full bg-accent/15 font-display text-xs font-semibold text-accent">{i + 1}</span>
                <span className="font-medium">{l.name}</span>
              </div>
              <ul className="flex flex-wrap gap-2">
                {l.items.map((it) => <li key={it} className="rounded-md border border-line bg-bg px-3 py-1.5 text-sm">{it}</li>)}
              </ul>
            </div>))}
        </div>
      </div>
    </section>
  );
}

type Tab = { label: string; src: StaticImageData; alt: string };
const TOUR: { id: string; title: string; body: string; points: string[]; link: [string, string]; tabs: Tab[] }[] = [
  { id: "see", title: "See every scheduled run", body: "Each job's schedule becomes a list of expected runs. You see what ran, what failed, what is late and what never started, across every server and cluster.",
    points: ["Run history with duration, exit code and output", "Reliability score per job", "Tags, owners and environments on every job"],
    link: ["/product", "Take the product tour"],
    tabs: [{ label: "Overview", src: shotOverview, alt: "Overview page with run states and jobs needing attention" },
           { label: "Jobs", src: shotJobs, alt: "Jobs list with last 30 runs per job" },
           { label: "Run detail", src: shotJob, alt: "A job's run history, timeline and captured output" }] },
  { id: "respond", title: "Respond before users notice", body: "Rules decide who hears about what, related failures become one incident, and on-call tools are paged and resolved for you.",
    points: ["Slack, Teams, email, webhooks, PagerDuty, Opsgenie", "Maintenance windows and per-team routing", "Pages resolve themselves when the job recovers"],
    link: ["/compare", "How it compares"],
    tabs: [{ label: "Alerting", src: shotAlerting, alt: "Alert channels, rules and recent deliveries" },
           { label: "Incidents", src: shotIncidents, alt: "Open incidents grouping related job failures" }] },
  { id: "inform", title: "Keep customers informed", body: "Public status pages show the scheduled work customers depend on, with 90-day history and incidents, updated as runs happen.",
    points: ["Choose exactly which jobs are public", "Scheduled maintenance shown in advance", "No extra tool to keep in sync"],
    link: ["/status/demo", "See an example status page"],
    tabs: [{ label: "Status page", src: shotStatus, alt: "Public status page with 90-day history per job" }] },
];

function TourBlock({ b, flip }: { b: (typeof TOUR)[number]; flip: boolean }) {
  const [i, setI] = useState(0);
  return (
    <div className={`grid gap-10 lg:grid-cols-[0.8fr_1.2fr] lg:items-center ${flip ? "lg:[&>*:first-child]:order-2 lg:grid-cols-[1.2fr_0.8fr]" : ""}`} id={b.id}>
      <div>
        <h3 className="font-display text-2xl font-semibold sm:text-3xl">{b.title}</h3>
        <p className="mt-3 text-mute">{b.body}</p>
        <ul className="mt-5 space-y-2 text-sm">{b.points.map((p) => <li key={p} className="flex gap-2"><Check size={16} className="mt-0.5 shrink-0 text-ok" aria-hidden />{p}</li>)}</ul>
        <Link href={b.link[0]} className="mt-6 inline-block text-sm font-medium text-accent hover:underline">{b.link[1]}</Link>
      </div>
      <div className="min-w-0">
        {b.tabs.length > 1 && <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label={b.title}>
          {b.tabs.map((t, k) => <button key={t.label} role="tab" aria-selected={i === k} onClick={() => setI(k)}
            className={`rounded-full border px-3 py-1 text-xs font-medium ${i === k ? "border-accent bg-accent/10 text-accent" : "border-line text-mute hover:text-ink"}`}>{t.label}</button>)}
        </div>}
        <ProductFrame src={b.tabs[i].src} alt={b.tabs[i].alt} />
      </div>
    </div>
  );
}

export function ProductTour() {
  return (
    <section className="border-b border-line" id="tour">
      <div className="mx-auto max-w-[1200px] space-y-24 px-6 py-20">
        <div className="max-w-[760px]">
          <h2 className="font-display text-3xl font-semibold sm:text-4xl">From &ldquo;did it run?&rdquo; to &ldquo;it&apos;s handled&rdquo;</h2>
          <p className="mt-4 text-[17px] text-mute">Expected runs, alerting, incidents and status pages are one product, sharing the same data from the first ping to the resolved page.</p>
        </div>
        {TOUR.map((b, k) => <TourBlock key={b.id} b={b} flip={k % 2 === 1} />)}
      </div>
    </section>
  );
}


const COPILOT_POINTS = [
  "Answers only from your telemetry: runs, logs, host metrics, Kubernetes events and deploys",
  "Secrets are redacted before anything reaches the model",
  "Suggests commands to investigate and never runs them",
  "Can use a model you host yourself, so nothing leaves your network",
];

/** The AI section — incident.io's "built on Nexus" slot, filled with what JobWatch's copilot actually does. */
export function CopilotSection() {
  return (
    <section className="border-b border-line bg-panel/40">
      <div className="mx-auto grid max-w-[1200px] gap-12 px-6 py-20 lg:grid-cols-[0.85fr_1.15fr] lg:items-center">
        <div>
          <span className="rounded-full bg-accent/10 px-3 py-1 text-xs font-medium text-accent">AI copilot</span>
          <h2 className="mt-4 font-display text-3xl font-semibold sm:text-4xl">Ask why a job failed. Get the evidence, not a guess.</h2>
          <p className="mt-4 text-[17px] text-mute">The copilot reads the failing runs, the host they ran on and what changed around them, then shows its reasoning: the evidence, what changed, what is affected and what to check first.</p>
          <ul className="mt-6 space-y-2.5 text-sm">{COPILOT_POINTS.map((p) => <li key={p} className="flex gap-2"><Check size={16} className="mt-0.5 shrink-0 text-ok" aria-hidden />{p}</li>)}</ul>
        </div>
        <div className="min-w-0">
          <ProductFrame src={shotCopilot} alt="The AI copilot explaining a failing job: likely cause with confidence, evidence, what changed, affected jobs and recommended actions" />
          <p className="mt-2 text-xs text-mute">Example answer from the demo data.</p>
        </div>
      </div>
    </section>
  );
}

const USES: { title: string; risk: string; catch: string }[] = [
  { title: "Database backups", risk: "The job stops after a credentials change or a host rebuild, and nobody looks until a restore is needed.",
    catch: "Missed the moment the 02:00 run does not start, and a run that exits 0 but reports an empty file fails its size check." },
  { title: "Data pipelines and ETL", risk: "An upstream export arrives late, the load runs on stale data or not at all, and reports go out wrong.",
    catch: "Late and missed runs per stage, run durations over time, alerts routed to the data team." },
  { title: "Billing and invoicing", risk: "A month-end run fails halfway, or runs twice, and finance hears about it from customers.",
    catch: "Start and finish signals with exit codes, timeouts for runs that hang, a page to the owner." },
  { title: "Kubernetes CronJobs", risk: "A CronJob is skipped behind its concurrency policy or a failing image, and the cluster stays green.",
    catch: "The in-cluster agent reads every CronJob, reports each run, and the failure reason from Kubernetes." },
  { title: "Certificates and renewals", risk: "The renewal timer was disabled during a migration; the certificate expires on a Sunday.",
    catch: "The agent reads each systemd timer's schedule and random delay, so a renewal that does not run is flagged weeks before expiry." },
  { title: "Nightly builds and CI schedules", risk: "A scheduled pipeline is silently turned off, and the nightly artifact is a month old.",
    catch: "One ping step at the end of the pipeline; JobWatch expects it every night." },
];

/** incident.io's customer-story grid, re-cast as the work JobWatch is built for (no invented customers). */
export function UseCases() {
  return (
    <section className="border-b border-line">
      <div className="mx-auto max-w-[1200px] px-6 py-20">
        <h2 className="max-w-[760px] font-display text-3xl font-semibold sm:text-4xl">Built for the jobs that fail quietly</h2>
        <p className="mt-4 max-w-[680px] text-[17px] text-mute">Scheduled work is where silent failures live. These are the jobs teams put on JobWatch first.</p>
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {USES.map((u) => (
            <article key={u.title} className="card flex flex-col p-5">
              <h3 className="font-display text-lg font-medium">{u.title}</h3>
              <p className="mt-2 text-sm text-mute">{u.risk}</p>
              <p className="mt-4 border-t border-line pt-4 text-sm"><span className="font-medium text-accent">JobWatch: </span>{u.catch}</p>
            </article>))}
        </div>
      </div>
    </section>
  );
}

/** Closing band, after incident.io's full-width final call to action. */
export function ClosingBand() {
  return (
    <section className="px-6 py-20">
      <div className="relative mx-auto max-w-[1200px] overflow-hidden rounded-2xl bg-ink px-8 py-16 text-center text-bg sm:px-16">
        <div className="relative">
          <h2 className="mx-auto max-w-[760px] font-display text-3xl font-semibold sm:text-5xl">Find out about the next missed run before your users do.</h2>
          <p className="mx-auto mt-4 max-w-[560px] text-bg/70">Five jobs free, no card. A ping URL working in two minutes, or the agent finding your crontabs for you.</p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link href="/signup" className="btn btn-primary px-6 py-3 text-[15px]">Start monitoring free</Link>
            <Link href="/compare" className="btn border-bg/25 bg-transparent px-6 py-3 text-[15px] text-bg hover:bg-bg/10">Compare alternatives</Link>
          </div>
        </div>
      </div>
    </section>
  );
}


const INTEGRATIONS: { group: string; items: string[] }[] = [
  { group: "Where jobs run", items: ["Linux cron", "systemd timers", "Kubernetes CronJobs", "GitHub Actions", "GitLab CI", "Jenkins", "Airflow", "Any script (ping URL)"] },
  { group: "Alerting and on-call", items: ["Slack", "Microsoft Teams", "Discord", "Telegram", "Email", "PagerDuty", "Opsgenie", "Datadog Events", "Signed webhooks"] },
  { group: "Observability and SIEM", items: ["Datadog metrics", "Datadog Logs", "Splunk HEC", "Any SIEM via signed webhook", "NATS and HTTPS signal streams"] },
  { group: "Identity", items: ["Okta", "Microsoft Entra ID", "Google Workspace", "Any SAML 2.0 or OIDC provider", "SCIM users and groups"] },
  { group: "As code", items: ["Terraform", "OpenTofu", "REST API"] },
  { group: "Move from", items: ["Healthchecks.io", "Cronitor"] },
];

/** R58: integrations grid (the section Datadog's homepage uses to show breadth). Names only, no logos. */
export function Integrations() {
  const total = INTEGRATIONS.reduce((n, g) => n + g.items.length, 0);
  return (
    <section className="border-b border-line" id="integrations">
      <div className="mx-auto max-w-[1200px] px-6 py-20">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h2 className="max-w-[640px] font-display text-3xl font-semibold sm:text-4xl">Fits into the tools you already run</h2>
          <p className="text-sm text-mute">{total} integrations; some depend on your plan (see Pricing)</p>
        </div>
        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {INTEGRATIONS.map((g) => (
            <div key={g.group} className="card p-4">
              <h3 className="text-sm font-semibold">{g.group}</h3>
              <ul className="mt-3 space-y-1.5 text-sm">{g.items.map((i) => <li key={i}>{i}</li>)}</ul>
            </div>))}
        </div>
      </div>
    </section>
  );
}
