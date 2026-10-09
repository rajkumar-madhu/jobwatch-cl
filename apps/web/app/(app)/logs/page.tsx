"use client";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, type Job } from "@/lib/api";
import { ts } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";

interface LogRow { execution_id: string; stream: string; content: string; job_id: string; job_name: string; status: string; host: string | null; scheduled_ts: string; exit_code: number | null }

function LogsInner() {
  const sp = useSearchParams();
  const [f, setF] = useState({ q: sp.get("q") ?? "", job_id: sp.get("job_id") ?? "", stream: sp.get("stream") ?? "", host: "", status: "", hours: "24" });
  const [applied, setApplied] = useState(f);
  const jobs = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?limit=200") });
  // R16: `since` used to be computed here, during render, and baked into the query key. Date.now()
  // differs on every render, so every render made a new key, which fetched, which re-rendered —
  // ~113 requests/second per open tab against the partitioned execution_logs table. The key is now
  // the applied *filters* (stable); the rolling window is computed inside queryFn, so each fetch —
  // including the 15s background poll — still gets a fresh "last N hours".
  // R38: 200 rows in one flat unpaginated list, with no way to reach anything past the cap, was a
  // real scannability and access problem — not just density. `until` walks the results backwards in
  // time (the API already accepted it; it just had no caller), so "Load more" fetches the next 200
  // older than the oldest row shown so far, same filters, same rolling window.
  const q = useInfiniteQuery({
    queryKey: ["logs", applied],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => {
      const since = new Date(Date.now() - Number(applied.hours) * 3600e3).toISOString();
      const params = new URLSearchParams(Object.entries({ q: applied.q, job_id: applied.job_id, stream: applied.stream, host: applied.host, status: applied.status, since, until: pageParam ?? "", limit: "200" }).filter(([, v]) => v));
      return api<{ items: LogRow[]; hosts: string[] }>(`/api/v1/logs/search?${params}`);
    },
    getNextPageParam: (last) => last.items.length < 200 ? undefined : new Date(new Date(last.items[last.items.length - 1].scheduled_ts).getTime() - 1).toISOString(),
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const hosts = q.data?.pages[0]?.hosts ?? [];
  const sel = "rounded-md border border-line bg-panel px-2 py-1.5 text-sm";
  const hl = (c: string) => applied.q ? c.split(new RegExp(`(${applied.q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig")).map((p, i) => p.toLowerCase() === applied.q.toLowerCase() ? <mark key={i} className="bg-warn/30">{p}</mark> : p) : c;
  // R38: an unbroken 200-row list had no visual rhythm to scan against. Group by the hour the run
  // was scheduled in — dense enough to matter for 15/30-minute jobs, sparse enough not to add noise
  // for anything slower — and label each break with a day-aware, locale-formatted heading.
  const hourKey = (iso: string) => { const d = new Date(iso); d.setMinutes(0, 0, 0); return d.getTime(); };
  const hourLabel = (iso: string) => {
    const d = new Date(iso), now = new Date(), yest = new Date(now); yest.setDate(now.getDate() - 1);
    const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();
    const day = sameDay(d, now) ? "Today" : sameDay(d, yest) ? "Yesterday" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `${day}, ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`;
  };
  return (
    <Page title="Logs">
      <form className="mb-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setApplied(f); }}>
        <input className={`${sel} min-w-[260px] flex-1 font-mono`} placeholder="Search stdout/stderr…  e.g. timeout, OOM, exit" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        <select className={sel} value={f.job_id} onChange={(e) => setF({ ...f, job_id: e.target.value })}><option value="">All jobs</option>{jobs.data?.items.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}</select>
        <select className={sel} value={f.stream} onChange={(e) => setF({ ...f, stream: e.target.value })}><option value="">stdout + stderr</option><option value="stderr">stderr only</option><option value="stdout">stdout only</option></select>
        <select className={sel} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Any outcome</option><option value="failed">Failed</option><option value="timeout">Timed out</option><option value="success">Success</option></select>
        <select className={sel} value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })}><option value="">All hosts</option>{hosts.map((h) => <option key={h}>{h}</option>)}</select>
        <select className={sel} value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })}><option value="1">Last hour</option><option value="24">Last 24h</option><option value="168">Last 7 days</option><option value="720">Last 30 days</option></select>
        <button className="btn btn-primary" type="submit">Search</button>
      </form>
      {q.error ? <ErrorBox error={q.error} /> : q.isLoading ? <Skeleton /> : items.length === 0
        ? <Empty title="No log lines match" hint="Logs come from cs-run wrappers and JSON heartbeats with stdout_tail / stderr_tail. Widen the time range or clear filters." />
        : <div className="space-y-2">{items.map((r, k) => (
            <div key={r.execution_id + r.stream + r.content.slice(0, 8)}>
              {(k === 0 || hourKey(r.scheduled_ts) !== hourKey(items[k - 1].scheduled_ts)) &&
                <div className="mb-2 mt-4 text-xs font-medium text-mute first:mt-0" data-testid="log-hour-heading">{hourLabel(r.scheduled_ts)}</div>}
              <details open={!!applied.q || r.stream === "stderr"} className="card bg-panel">
                <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
                  <span className="font-mono text-xs text-mute">{ts(r.scheduled_ts)}</span><Link href={`/jobs/${r.job_id}`} className="font-medium hover:underline">{r.job_name}</Link>
                  <Status s={r.status} /><span className={`font-mono text-xs ${r.stream === "stderr" ? "text-bad" : "text-mute"}`}>{r.stream}</span>{r.host && <span className="font-mono text-xs text-mute">{r.host}</span>}
                  <span className="ml-auto font-mono text-xs text-mute">exit {r.exit_code ?? "—"}</span></summary>
                <pre className={`max-h-72 overflow-auto whitespace-pre-wrap border-t border-line px-3 py-2 font-mono text-xs ${r.stream === "stderr" ? "text-bad" : ""}`}>{hl(r.content)}</pre>
              </details>
            </div>))}
            {q.hasNextPage && <button className="btn w-full justify-center" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>{q.isFetchingNextPage ? "Loading…" : "Load more"}</button>}
          </div>}
    </Page>
  );
}
export default function LogsPage() { return <Suspense><LogsInner /></Suspense>; }
