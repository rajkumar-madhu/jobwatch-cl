"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, AlertTriangle, BarChart3, Bell, Boxes, CreditCard, FileText, LayoutDashboard, ListChecks, LogOut,
  Menu, Network, Plug, Search, Server, Settings, Sparkles, Siren, PhoneCall, X,
} from "lucide-react";
import { api, logout, type Overview } from "@/lib/api";
import { ThemeToggle } from "@/components/theme-toggle";

type Item = { href: string; label: string; icon: React.ElementType };
// Grouped by the question the user is asking: what is broken, where does it run, why, and how is it set up.
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Monitor", items: [
    { href: "/", label: "Overview", icon: LayoutDashboard }, { href: "/jobs", label: "Jobs", icon: ListChecks },
    { href: "/failures", label: "Failures", icon: AlertTriangle }, { href: "/incidents", label: "Incidents", icon: Siren }, { href: "/oncall", label: "On-call", icon: PhoneCall },
    { href: "/logs", label: "Logs", icon: FileText }] },
  { title: "Infrastructure", items: [
    { href: "/agents", label: "Servers & agents", icon: Server }, { href: "/kubernetes", label: "Kubernetes", icon: Boxes },
    { href: "/topology", label: "Topology", icon: Network }] },
  { title: "Insights", items: [
    { href: "/analytics", label: "Analytics", icon: BarChart3 }, { href: "/copilot", label: "AI Copilot", icon: Sparkles }] },
  { title: "Configure", items: [
    { href: "/alerting", label: "Alerting", icon: Bell }, { href: "/integrations", label: "Integrations", icon: Plug },
    { href: "/settings", label: "Settings", icon: Settings }, { href: "/billing", label: "Billing", icon: CreditCard }] },
];

function NavList({ bad, onNav }: { bad: number; onNav?: () => void }) {
  const path = usePathname();
  return (
    <nav className="flex-1 space-y-4 overflow-y-auto px-2 pb-4" aria-label="Main">
      {GROUPS.map((g) => (
        <div key={g.title}>
          <div className="px-2 pb-1 text-[11px] font-medium text-mute/80">{g.title}</div>
          {g.items.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} onClick={onNav} aria-current={active ? "page" : undefined}
                className={clsx("group relative flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px]",
                  active ? "bg-raised text-ink" : "text-ink/75 hover:bg-raised/60 hover:text-ink")}>
                {active && <span className="absolute -left-2 top-1.5 bottom-1.5 w-0.5 rounded-full bg-accent" aria-hidden />}
                <Icon size={15} className={active ? "text-accent" : "text-mute group-hover:text-ink"} aria-hidden />
                <span className="flex-1">{label}</span>
                {href === "/failures" && bad > 0 && <span className="rounded-full bg-bad/15 px-1.5 text-[11px] font-medium text-bad">{bad}</span>}
              </Link>);
          })}
        </div>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <span className="flex items-center gap-2">
      <span className="grid h-6 w-6 place-items-center rounded-md bg-accent/15 text-accent"><Activity size={14} aria-hidden /></span>
      <span className="font-display text-[15px] font-semibold tracking-tight">JobWatch</span>
    </span>
  );
}

function Footer({ onSignOut }: { onSignOut: () => void }) {
  return (
    <div className="space-y-1 border-t border-line px-2 py-3">
      <button aria-label="Open command palette" className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-mute hover:bg-raised hover:text-ink"
        onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}>
        <Search size={14} aria-hidden /><span className="flex-1 text-left">Search and commands</span><span className="kbd">⌘K</span>
      </button>
      <ThemeToggle className="w-full" />
      <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-mute hover:bg-raised hover:text-ink" onClick={onSignOut}>
        <LogOut size={14} aria-hidden />Sign out
      </button>
    </div>
  );
}

export function Sidebar() {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  const { data } = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/v1/analytics/overview") });
  const bad = (data?.by_status.failed ?? 0) + (data?.by_status.missed ?? 0) + (data?.by_status.timeout ?? 0);
  const signOut = async () => { await logout(); location.href = "/welcome"; };
  return (
    <>
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-panel/95 px-4 py-3 backdrop-blur lg:hidden">
        <Brand />
        <button aria-label="Open menu" onClick={() => setOpen(true)} className="btn px-2"><Menu size={16} aria-hidden />
          {bad > 0 && <span className="rounded-full bg-bad/15 px-1.5 text-[11px] text-bad">{bad}</span>}</button>
      </header>
      {open && <div className="fixed inset-0 z-40 bg-black/50 lg:hidden" onClick={() => setOpen(false)}>
        <aside className="app-sidebar flex h-full w-72 flex-col border-r border-line bg-panel" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center justify-between px-4 py-4"><Brand /><button aria-label="Close menu" className="text-mute hover:text-ink" onClick={() => setOpen(false)}><X size={18} /></button></div>
          <NavList bad={bad} onNav={() => setOpen(false)} /><Footer onSignOut={signOut} />
        </aside></div>}
      <aside className="app-sidebar sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-panel lg:flex">
        <div className="px-4 py-4"><Brand /></div>
        <NavList bad={bad} />
        <Footer onSignOut={signOut} />
      </aside>
    </>
  );
}
