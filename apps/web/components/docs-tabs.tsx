"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

/** R61: Guides | API reference, the two areas of the docs (as Datadog splits Docs and API). */
export function DocsTabs() {
  const path = usePathname();
  const api = path.startsWith("/docs/api");
  const tab = (href: string, label: string, active: boolean) => (
    <Link href={href} aria-current={active ? "page" : undefined}
      className={`-mb-[9px] border-b-2 px-1 pb-2 pt-1 text-sm font-medium ${active ? "border-accent text-ink" : "border-transparent text-mute hover:text-ink"}`}>{label}</Link>);
  return <nav aria-label="Documentation areas" className="flex gap-5">{tab("/docs", "Guides", !api)}{tab("/docs/api", "API reference", api)}</nav>;
}
