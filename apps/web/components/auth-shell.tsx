"use client";
/* R51: shared layout for sign-in and sign-up. Form on the left; on wide screens, a panel showing what
   the product does (the schedule tape), not testimonials JobWatch does not have. Every credential step
   happens on Keycloak; these pages only choose the route (provider, SSO, email). */
import Link from "next/link";
import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { ScheduleTape } from "@/components/marketing";
import { ThemeToggle } from "@/components/theme-toggle";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "";
export type Provider = { alias: string; label: string };

export function useProviders(): Provider[] {
  const [p, setP] = useState<Provider[]>([]);
  useEffect(() => {
    fetch(`${API}/auth/providers`).then((r) => (r.ok ? r.json() : { social: [] })).then((d) => setP(d.social ?? [])).catch(() => setP([]));
  }, []);
  return p;
}

export function useNext(fallback: string): string {
  const [n, setN] = useState(fallback);
  useEffect(() => {
    const v = new URLSearchParams(location.search).get("next");
    if (v && v.startsWith("/") && !v.startsWith("//")) setN(v);   // the API re-checks this too (safe_next)
  }, []);
  return n;
}

function Mark({ alias }: { alias: string }) {
  const letter = { google: "G", github: "GH", microsoft: "M", gitlab: "GL", bitbucket: "B" }[alias] ?? alias[0]?.toUpperCase();
  return <span className="grid h-5 w-5 place-items-center rounded bg-raised text-[10px] font-semibold text-mute" aria-hidden>{letter}</span>;
}

export function ProviderButtons({ providers, href }: { providers: Provider[]; href: (alias: string) => string }) {
  if (!providers.length) return null;
  return (
    <div className="space-y-2">
      {providers.map((p) => (
        <a key={p.alias} href={href(p.alias)} className="btn w-full justify-start gap-3 py-2.5"><Mark alias={p.alias} /><span className="flex-1 text-center">Continue with {p.label}</span><span className="w-5" /></a>))}
    </div>
  );
}

export function Divider({ label = "or" }: { label?: string }) {
  return <div className="my-5 flex items-center gap-3 text-xs text-mute"><span className="h-px flex-1 bg-line" />{label}<span className="h-px flex-1 bg-line" /></div>;
}

const POINTS = ["A ping URL working in two minutes", "The agent finds your crontabs and systemd timers", "Import from Healthchecks.io or Cronitor, keep your URLs"];

export function AuthShell({ title, subtitle, children, footer }: { title: string; subtitle?: React.ReactNode; children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(420px,1fr)_1.15fr]">
      <div className="flex flex-col px-6 py-8 sm:px-12">
        <div className="flex items-center justify-between">
          <Link href="/welcome" className="flex items-center gap-2 font-display font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-accent/15 text-accent"><Activity size={15} aria-hidden /></span>JobWatch</Link>
          <ThemeToggle />
        </div>
        <div className="mx-auto flex w-full max-w-[380px] flex-1 flex-col justify-center py-12">
          <h1 className="font-display text-[28px] font-semibold leading-tight">{title}</h1>
          {subtitle && <p className="mt-2 text-sm text-mute">{subtitle}</p>}
          <div className="mt-8">{children}</div>
          <div className="mt-8 text-sm text-mute">{footer}</div>
        </div>
      </div>
      <aside className="relative hidden overflow-hidden border-l border-line bg-panel lg:flex lg:flex-col lg:justify-center lg:px-12" aria-label="About JobWatch">
        <div className="relative">
          <h2 className="max-w-[460px] font-display text-3xl font-semibold leading-tight">Every scheduled run, accounted for.</h2>
          <p className="mt-3 max-w-[460px] text-mute">JobWatch expects each run before it is due, so a job that never starts is caught on time, not found weeks later.</p>
          <div className="mt-8 [&_figure]:shadow-[0_30px_80px_-40px_rgb(0_0_0/.45)]"><ScheduleTape callout={false} /></div>
          <ul className="mt-8 grid gap-3 text-sm">{POINTS.map((p) => <li key={p} className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />{p}</li>)}</ul>
        </div>
      </aside>
    </main>
  );
}
