"use client";
import Link from "next/link";
import { useState } from "react";
import { Activity, Check, CircleDashed, Menu, Minus, X } from "lucide-react";
import type { Cell } from "@/lib/compare";
import { ThemeToggle } from "@/components/theme-toggle";

const LINKS: [string, string][] = [["/product", "Product"], ["/docs", "Docs"], ["/compare", "Compare"], ["/welcome#pricing", "Pricing"], ["/tools/cron", "Cron checker"]];

export function SiteNav() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1200px] items-center justify-between gap-6 px-6 py-3.5">
        <Link href="/welcome" className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent/15 text-accent"><Activity size={15} aria-hidden /></span>
          <span className="font-display text-[15px] font-semibold">WeCrew JobWatch</span>
        </Link>
        <nav className="hidden items-center gap-6 text-sm md:flex" aria-label="Site">
          {LINKS.map(([h, l]) => <Link key={h} href={h} className="text-mute hover:text-ink">{l}</Link>)}
        </nav>
        <div className="hidden items-center gap-2 md:flex">
          <ThemeToggle />
          <Link href="/login" className="btn border-transparent bg-transparent">Sign in</Link>
          <Link href="/signup" className="btn btn-primary">Start free</Link>
        </div>
        <button className="btn px-2 md:hidden" aria-label="Open menu" onClick={() => setOpen(!open)}>{open ? <X size={16} /> : <Menu size={16} />}</button>
      </div>
      {open && <nav className="border-t border-line px-6 py-3 md:hidden" aria-label="Site (mobile)">
        {LINKS.map(([h, l]) => <Link key={h} href={h} onClick={() => setOpen(false)} className="block py-2 text-sm">{l}</Link>)}
        <div className="mt-2 flex gap-2"><Link href="/login" className="btn flex-1">Sign in</Link><Link href="/signup" className="btn btn-primary flex-1">Start free</Link></div>
        <ThemeToggle className="mt-2" />
      </nav>}
    </header>
  );
}

const FOOT: [string, [string, string][]][] = [
  ["Product", [["/product", "Feature tour"], ["/product#security", "Security"], ["/welcome#pricing", "Pricing"], ["/status/demo", "Status page example"]]],
  ["Docs", [["/docs/quickstart", "Quick start"], ["/docs/pings", "Pings and heartbeat API"], ["/docs/linux-agent", "Linux agent"], ["/docs/api", "API reference"]]],
  ["Compare", [["/compare", "JobWatch vs alternatives"], ["/compare#behind", "Where we are behind"]]],
  ["Tools", [["/tools/cron", "Cron expression checker"], ["/login", "Sign in"]]],
];

export function SiteFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-[1200px] gap-10 px-6 py-12 text-sm sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr_1fr]">
        <div>
          <div className="font-display font-semibold">WeCrew JobWatch</div>
          <p className="mt-2 max-w-xs text-mute">Monitoring for scheduled work. Run it on our cloud or inside your own network.</p>
        </div>
        {FOOT.map(([h, links]) => (
          <div key={h}><div className="font-medium">{h}</div>
            <ul className="mt-3 space-y-2 text-mute">{links.map(([href, l]) => <li key={href + l}><Link href={href} className="hover:text-ink">{l}</Link></li>)}</ul></div>))}
      </div>
    </footer>
  );
}

/* ---- Schedule tape ---------------------------------------------------------------------------
   The product's core idea drawn literally: every row is a job, every square a run the schedule
   promised. Filled = it happened (colour = outcome), dashed = it never started, hollow = still to
   come. The cyan line is now. */
type Slot = { h: number; s: "ok" | "late" | "failed" | "missed" };
export type TapeRow = { name: string; where: string; slots: Slot[]; next: number[] };

export const DEMO_TAPE: TapeRow[] = [
  { name: "nightly-database-backup", where: "cron on db-01", slots: [{ h: 2, s: "ok" }], next: [] },
  { name: "payments/settle-batch", where: "Kubernetes CronJob", slots: [{ h: 0.25, s: "ok" }, { h: 4.25, s: "missed" }, { h: 8.25, s: "ok" }, { h: 12.25, s: "ok" }], next: [16.25, 20.25] },
  { name: "billing-reconciliation", where: "systemd timer", slots: [0, 2, 4, 6, 8, 10, 12, 14].map((h) => ({ h, s: h === 8 ? "failed" as const : "ok" as const })), next: [16, 18, 20, 22] },
  { name: "market-data-import", where: "GitHub Actions", slots: [6, 7, 8, 9, 10, 11, 12, 13, 14].map((h) => ({ h, s: h === 9 ? "late" as const : "ok" as const })), next: [15, 16, 17, 18] },
  { name: "s3-log-archive", where: "heartbeat URL", slots: [{ h: 1, s: "ok" }, { h: 13, s: "ok" }], next: [] },
];

export function ScheduleTape({ rows = DEMO_TAPE, now = 14.6, callout = true }: { rows?: TapeRow[]; now?: number; callout?: boolean }) {
  const pct = (h: number) => `${(h / 24) * 100}%`;
  return (
    <figure className="card overflow-hidden" aria-label="A day of scheduled runs: one missed, one failed, one late">
      <div className="overflow-x-auto"><div className="min-w-[720px]">
      <div className="grid grid-cols-[minmax(150px,220px)_1fr] border-b border-line text-[11px] text-mute">
        <div className="px-4 py-2">Job</div>
        <div className="relative mr-4 h-8">
          {[0, 4, 8, 12, 16, 20].map((h) => <span key={h} className="absolute top-2 -translate-x-1/2 tabular-nums" style={{ left: pct(h) }}>{String(h).padStart(2, "0")}:00</span>)}
        </div>
      </div>
      <div className="tape-reveal">
        {rows.map((r, i) => (
          <div key={r.name} className="grid grid-cols-[minmax(150px,220px)_1fr] items-center border-b border-line last:border-0">
            <div className="min-w-0 px-4 py-3">
              <div className="truncate text-[13px] font-medium">{r.name}</div>
              <div className="truncate text-xs text-mute">{r.where}</div>
            </div>
            <div className="relative mr-4 h-11">
              <div className="absolute inset-x-0 top-1/2 h-px bg-line" />
              {[4, 8, 12, 16, 20].map((h) => <div key={h} className="absolute inset-y-2 w-px bg-line/60" style={{ left: pct(h) }} />)}
              {r.slots.map((s) => <span key={s.h} className={`tape-slot ${s.s}`} style={{ left: pct(s.h) }} title={`${s.s} at ${Math.floor(s.h)}:${String(Math.round((s.h % 1) * 60)).padStart(2, "0")}`} />)}
              {r.slots.filter((s) => s.s === "missed").map((s) => <span key={`l${s.h}`} className="absolute top-0.5 ml-3 whitespace-nowrap rounded bg-bad/15 px-1.5 text-[10.5px] font-medium text-bad" style={{ left: pct(s.h) }}>missed</span>)}
              {r.next.map((h) => <span key={h} className="tape-slot expected" style={{ left: pct(h) }} />)}
              <span className={i === 0 ? "tape-now tape-now-label" : "tape-now"} style={{ left: pct(now) }} aria-hidden />
            </div>
          </div>
        ))}
      </div>
      </div></div>
      {callout && <figcaption className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-line bg-raised/40 px-4 py-3 text-xs text-mute">
        <span className="text-ink"><span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm border-[1.5px] border-dashed border-bad align-middle" />payments/settle-batch never started at 04:15. Slack #payments-oncall was alerted at 04:25.</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-ok align-middle" />ran</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-warn align-middle" />late</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm bg-bad align-middle" />failed</span>
        <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm border-[1.5px] border-mute/60 align-middle" />expected</span>
      </figcaption>}
    </figure>
  );
}

export function Mark({ c }: { c: Cell }) {
  if (c === "yes") return <Check size={16} className="text-ok" aria-label="Yes" />;
  if (c === "no") return <Minus size={16} className="text-mute" aria-label="No" />;
  if (c === "partial") return <CircleDashed size={15} className="text-warn" aria-label="Partly" />;
  return <span className="text-xs text-mute">{c}</span>;
}

