"use client";
import Link from "next/link";
import { useState } from "react";
import { SiteNav, SiteFooter, Mark } from "@/components/marketing";
import { CHECKED_ON, GROUPS, SOURCES, VENDORS } from "@/lib/compare";

// When an alternative is the better choice. Saying so is what makes the rest of the table credible.
const CHOOSE: [string, string][] = [
  ["Healthchecks.io", "You want a mature, open-source ping monitor with a generous free tier and do not need agents, Kubernetes discovery or per-run records. (Switching later is one import: ping URLs keep their paths.)"],
  ["Cronitor", "You want a polished hosted service with SDKs in many languages and are comfortable paying per monitor and per user. (Switching later is one import: cronitor.link URLs keep their paths.)"],
  ["Better Stack", "You already use it for uptime, logs and on-call, and heartbeats are one more check in the same place."],
  ["Sentry Crons", "Your scheduled work is application code already reporting to Sentry, and you want failures next to the stack traces."],
  ["Uptime Kuma", "You need a free, self-hosted dashboard for a home lab or a handful of services, run by one person."],
];

const BEHIND: [string, string][] = [
  ["Phone and SMS paging", "JobWatch runs on-call rotas and escalations, but pages by email, chat and webhooks; it has no phone calls, SMS or mobile app. Hand off to PagerDuty or Opsgenie for those."],
  ["Open source", "JobWatch is not released under an open-source licence. If that is a requirement, Healthchecks.io and Uptime Kuma are."],
  ["Track record", "JobWatch is young. The others have years of production history and much larger user bases."],
];

export default function Compare() {
  const [only, setOnly] = useState<string | null>(null);
  const shown = only ? VENDORS.filter((v) => v.key === "jw" || v.key === only) : VENDORS;
  return (
    <div className="min-h-screen">
      <SiteNav />
      <main>
        <section className="border-b border-line">
          <div className="mx-auto max-w-[1200px] px-6 pb-12 pt-16">
            <h1 className="max-w-[780px] font-display text-[40px] font-semibold leading-[1.08] sm:text-[52px]">JobWatch compared with the tools teams usually consider</h1>
            <p className="mt-5 max-w-[680px] text-[16px] text-mute">
              Checked against each vendor&apos;s public pricing and documentation on {CHECKED_ON}. Where we could not confirm a capability we mark it as partial and say why.
              If something here is wrong, tell us and we will correct it.
            </p>
            <div className="mt-8 flex flex-wrap gap-2" role="group" aria-label="Compare JobWatch with">
              <button onClick={() => setOnly(null)} className={`btn ${only === null ? "border-accent text-accent" : ""}`} aria-pressed={only === null}>All tools</button>
              {VENDORS.filter((v) => v.key !== "jw").map((v) => (
                <button key={v.key} onClick={() => setOnly(v.key)} className={`btn ${only === v.key ? "border-accent text-accent" : ""}`} aria-pressed={only === v.key}>vs {v.name}</button>))}
            </div>
          </div>
        </section>

        <section className="mx-auto max-w-[1200px] px-6 py-12">
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[820px] table-fixed text-sm">
              <thead className="sticky top-0 z-10 bg-panel"><tr className="border-b border-line text-left text-xs text-mute">
                <th className="w-[38%] px-4 py-3 font-normal">Capability</th>
                {shown.map((v) => <th key={v.key} className={`px-3 py-3 text-center font-normal ${v.key === "jw" ? "text-accent" : ""}`}>{v.name}</th>)}
              </tr></thead>
              {GROUPS.map((g) => (
                <tbody key={g.title}>
                  <tr className="border-b border-line bg-raised/50"><th colSpan={shown.length + 1} className="px-4 py-2 text-left font-display text-[13px] font-medium">{g.title}</th></tr>
                  {g.rows.map((r) => (
                    <tr key={r.label} className="border-b border-line align-top">
                      <td className="px-4 py-3">
                        <div>{r.label}</div>
                        {r.help && <div className="mt-0.5 text-xs text-mute">{r.help}</div>}
                        {r.note && <div className="mt-1 text-xs italic text-mute">{r.note}</div>}
                      </td>
                      {shown.map((v) => <td key={v.key} className={`px-3 py-3 ${v.key === "jw" ? "bg-accent/[.05]" : ""}`}><div className="flex justify-center text-center"><Mark c={r.cells[v.key]} /></div></td>)}
                    </tr>))}
                </tbody>))}
            </table>
          </div>
          <p className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-mute">
            <span className="inline-flex items-center gap-1.5"><Mark c="yes" />Available</span>
            <span className="inline-flex items-center gap-1.5"><Mark c="partial" />Partly, or depends on plan</span>
            <span className="inline-flex items-center gap-1.5"><Mark c="no" />Not available</span>
          </p>
          <details className="mt-6 text-sm"><summary className="cursor-pointer text-mute hover:text-ink">Sources</summary>
            <ul className="mt-3 space-y-1.5">{SOURCES.map(([l, u]) => <li key={u}><a href={u} rel="noopener noreferrer" target="_blank" className="text-accent hover:underline">{l}</a></li>)}</ul>
          </details>
        </section>

        <section className="border-y border-line bg-panel/40" id="behind">
          <div className="mx-auto grid max-w-[1200px] gap-12 px-6 py-16 lg:grid-cols-[1fr_2fr]">
            <div>
              <h2 className="font-display text-3xl font-semibold">Where JobWatch is behind</h2>
              <p className="mt-3 text-mute">If any of these is a requirement today, one of the alternatives may suit you better.</p>
            </div>
            <dl className="grid gap-x-10 gap-y-6 sm:grid-cols-2">
              {BEHIND.map(([t, d]) => <div key={t} className="border-t border-line pt-4"><dt className="font-medium">{t}</dt><dd className="mt-1.5 text-sm text-mute">{d}</dd></div>)}
            </dl>
          </div>
        </section>

        <section className="mx-auto max-w-[1200px] px-6 py-16">
          <h2 className="font-display text-3xl font-semibold">When to choose something else</h2>
          <dl className="mt-8 divide-y divide-line">
            {CHOOSE.map(([t, d]) => (
              <div key={t} className="grid gap-2 py-4 sm:grid-cols-[220px_1fr]"><dt className="font-medium">{t}</dt><dd className="text-sm text-mute">{d}</dd></div>))}
            <div className="grid gap-2 py-4 sm:grid-cols-[220px_1fr]"><dt className="font-medium text-accent">JobWatch</dt>
              <dd className="text-sm text-mute">You run scheduled work across servers, Kubernetes and pipelines, need every expected run accounted for, and want it isolated per tenant or inside your own network.</dd></div>
          </dl>
          <div className="mt-10 flex flex-wrap gap-3"><Link href="/login" className="btn btn-primary px-5 py-2.5">Start monitoring free</Link><Link href="/product" className="btn px-5 py-2.5">Take the product tour</Link></div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
