"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";

type Entry = { slug: string; title: string; text: string };

/** R61: the docs home opens with search, as Datadog's docs do. */
export function DocsSearchHero({ index }: { index: Entry[] }) {
  const [q, setQ] = useState("");
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? index.filter((e) => (e.title + " " + e.text).toLowerCase().includes(s)).slice(0, 6) : [];
  }, [q, index]);
  return (
    <div className="relative max-w-[640px]">
      <label className="relative block">
        <span className="sr-only">Search the documentation</span>
        <Search size={18} className="absolute left-4 top-1/2 -translate-y-1/2 text-mute" aria-hidden />
        <input className="input py-3 text-base" style={{ paddingLeft: "2.75rem" }} placeholder="Search: ping URL, systemd timer, Splunk, SCIM…"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </label>
      {q && (
        <ul className="overlay absolute inset-x-0 z-10 mt-2 border border-line bg-panel p-2" aria-label="Search results">
          {hits.length ? hits.map((h) => (
            <li key={h.slug}><Link href={h.slug === "api" ? "/docs/api" : `/docs/${h.slug}`} className="block rounded-md px-3 py-2 hover:bg-raised">
              <span className="font-medium">{h.title}</span><span className="block truncate text-sm text-mute">{h.text}</span></Link></li>))
            : <li className="px-3 py-2 text-sm text-mute">No pages match. Try a shorter word.</li>}
        </ul>)}
    </div>
  );
}
