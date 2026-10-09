"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api, type Job, type Overview, type Incident, type MonitoringGaps } from "@/lib/api";
import { ago, dur } from "@/lib/format";
import { Page, Status, Skeleton, ErrorBox, Empty } from "@/components/ui";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

export default function OverviewPage() {
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/v1/analytics/overview") });
  const attention = useQuery({ queryKey: ["jobs", "attention"], queryFn: async () => {
    const all = await api<{ items: Job[] }>("/api/v1/jobs?limit=200");
    return all.items.filter((j) => ["failed", "missed", "late", "timeout"].includes(j.status)).sort((a, b) => (b.last_run_at ?? "").localeCompare(a.last_run_at ?? ""));
  }});
  const running = useQuery({ queryKey: ["jobs", "running"], queryFn: () => api<{ items: Job[] }>("/api/v1/jobs?status=running&limit=50") });
  const inc = useQuery({ queryKey: ["incidents", "open"], queryFn: () => api<Incident[]>("/api/v1/incidents?status=open&limit=5") });
  const series = useQuery({ queryKey: ["series", 7], queryFn: () => api<any>("/api/v1/analytics/series?days=7") });
  // R25: if the platform itself was not watching, say so — those slots are "unobserved", not missed.
  const gaps = useQuery({ queryKey: ["platform", "gaps"], staleTime: 300_000, queryFn: () => api<MonitoringGaps>("/api/v1/platform/monitoring-gaps") });
  const recentGaps = (gaps.data?.gaps ?? []).filter((g) => Date.now() - new Date(g.ended_at).getTime() < 7 * 86_400_000);
  const worstGapService = recentGaps.length > 1
    ? recentGaps.reduce((a, b) => (new Date(b.ended_at).getTime() - new Date(b.started_at).getTime()) > (new Date(a.ended_at).getTime() - new Date(a.started_at).getTime()) ? b : a).service
    : undefined;
  const pts = (series.data?.points ?? []).map((p: any) => ({ t: new Date(p.t).toLocaleDateString(undefined, { weekday: "short" }), ok: p.ok, failed: p.failed, missed: p.missed }));

  if (ov.error) return <Page title="Overview"><ErrorBox error={ov.error} /></Page>;
  const d = ov.data;
  const bad = d ? (d.by_status.failed ?? 0) + (d.by_status.missed ?? 0) + (d.by_status.timeout ?? 0) : 0;
  const late = d?.by_status.late ?? 0;

  return (
    <Page title="Overview" subtitle="Every scheduled run across your servers, clusters and pipelines.">
      {/* One sentence that answers "is anything wrong right now?" */}
      <p className="mb-6 text-[15px]">
        {!d ? <span className="skeleton inline-block h-5 w-72" /> : bad + late === 0
          ? <>All <b>{d.total_jobs}</b> jobs are healthy. {d.executions_today} runs today, {d.success_rate_today ?? 100}% succeeded.</>
          : <><b className="text-bad">{bad} job{bad === 1 ? "" : "s"} need attention</b>{late > 0 && <>, {late} running late</>}. {d.executions_today} runs today, {d.success_rate_today}% succeeded.</>}
      </p>

      {recentGaps.length > 0 && <div className="mb-6 rounded-md border border-warn/40 bg-warn/10 px-4 py-2.5 text-sm" data-testid="monitoring-gap">
        {recentGaps.length === 1
          ? <><b>Monitoring gap on our side</b> — {recentGaps[0].service} was not watching for {dur(new Date(recentGaps[0].ended_at).getTime() - new Date(recentGaps[0].started_at).getTime())} ({ago(recentGaps[0].ended_at)}).</>
          : <details>
              <summary className="cursor-pointer list-none"><b>Monitoring gap on our side</b> — {recentGaps.length} gaps in the last 7 days, worst {dur(Math.max(...recentGaps.map((g) => new Date(g.ended_at).getTime() - new Date(g.started_at).getTime())))} ({worstGapService}). <span className="text-mute underline">Show all</span></summary>
              <ul className="mt-2 space-y-0.5">
                {recentGaps.map((g) => <li key={g.id}>{g.service} was not watching for {dur(new Date(g.ended_at).getTime() - new Date(g.started_at).getTime())} ({ago(g.ended_at)})</li>)}
              </ul>
            </details>}
        {" "}{gaps.data!.unobserved_slots_30d} scheduled run{gaps.data!.unobserved_slots_30d === 1 ? "" : "s"} could not be observed and were not alerted on.
      </div>}

      {d && <div className="card mb-8 grid grid-cols-3 divide-line overflow-hidden sm:grid-cols-5 lg:grid-cols-9 [&>*]:border-line [&>*]:border-b sm:[&>*]:border-r">
        {([["healthy", "Healthy"], ["running", "Running"], ["late", "Late"], ["missed", "Missed"], ["failed", "Failed"], ["timeout", "Timed out"], ["paused", "Paused"]] as [string, string][]).map(([k, l]) => {
          const n = d.by_status[k] ?? 0; const hot = n > 0 && ["failed", "missed", "timeout"].includes(k);
          return (<Link key={k} href={`/jobs?status=${k}`} className={`px-4 py-3 hover:bg-raised ${hot ? "bg-bad/10" : ""}`}>
            <div className={`font-display text-2xl font-semibold tabular-nums ${hot ? "text-bad" : ""}`}>{n}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs text-mute"><span className={`dot dot-${k}`} />{l}</div></Link>);
        })}
        <div className="px-4 py-3"><div className="whitespace-nowrap font-display text-2xl font-semibold tabular-nums">{(d as any).mttd_min != null ? dur((d as any).mttd_min * 60_000) : "—"}</div><div className="mt-0.5 text-xs text-mute" title="Mean time to detect, last 30 days">Time to detect</div></div>
        <div className="px-4 py-3"><div className="whitespace-nowrap font-display text-2xl font-semibold tabular-nums">{(d as any).mttr_min != null ? dur((d as any).mttr_min * 60_000) : "—"}</div><div className="mt-0.5 text-xs text-mute" title="Mean time to recover, last 30 days">Time to recover</div></div>
      </div>}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[3fr_2fr]">
        <section>
          <h2 className="mb-2 text-sm font-medium">Needs attention</h2>
          {attention.isLoading ? <Skeleton rows={4} /> : attention.data?.length === 0
            ? <Empty title="Nothing failed, missed or late" hint="Jobs that break will show up here first." />
            : <div className="tbl card">
                {attention.data?.map((j) => (
                  <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[1fr_120px_130px]">
                    <div className="truncate font-medium">{j.name}<span className="ml-2 font-normal text-mute">{j.schedule_human}</span></div>
                    <Status s={j.status} />
                    <span className="text-mute">{ago(j.last_run_at)}</span>
                  </Link>
                ))}
              </div>}

          <h2 className="mb-2 mt-8 text-sm font-medium">Last 7 days</h2>
          <div className="card h-40 p-3">{pts.length > 0 ? <ResponsiveContainer><BarChart data={pts}><XAxis dataKey="t" tick={{ fontSize: 11, fill: "rgb(var(--mute))" }} axisLine={{ stroke: "rgb(var(--line))" }} tickLine={false} /><Tooltip cursor={{ fill: "rgb(var(--raised))" }} contentStyle={{ background: "rgb(var(--panel))", border: "1px solid rgb(var(--line))", borderRadius: 6, fontSize: 12 }} labelStyle={{ color: "rgb(var(--ink))" }} /><Bar dataKey="ok" stackId="a" fill="rgb(var(--ok))" name="succeeded" /><Bar dataKey="failed" stackId="a" fill="rgb(var(--bad))" name="failed" /><Bar dataKey="missed" stackId="a" fill="rgb(var(--warn))" name="missed" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer> : <div className="skeleton h-full" />}</div>
          <h2 className="mb-2 mt-8 text-sm font-medium">Running now</h2>
          {running.data?.items.length === 0 ? <p className="text-sm text-mute">Nothing is executing at the moment.</p>
            : <div className="tbl card">{running.data?.items.map((j) => (
                <Link key={j.id} href={`/jobs/${j.id}`} className="row grid-cols-[1fr_auto]"><span className="font-medium">{j.name}</span><span className="text-mute">started {ago(j.last_run_at)}</span></Link>))}</div>}
        </section>

        <section className="space-y-8">
          <div>
            <h2 className="mb-2 text-sm font-medium">Open incidents</h2>
            {inc.data?.length === 0 ? <p className="text-sm text-mute">No open incidents.</p>
              : <div className="tbl card">{inc.data?.map((i) => (
                  <Link key={i.id} href={`/incidents/${i.id}`} className="row grid-cols-[1fr_auto]"><span className="truncate">{i.title}</span><span className="text-xs text-mute">{ago(i.started_at)}</span></Link>))}</div>}
          </div>
          <div>
            <h2 className="mb-2 text-sm font-medium">Slowest jobs, 7 days (p95)</h2>
            <ul className="tbl card">{d?.top_slowest_7d.map((r) => (
              <li key={r.name} className="row grid-cols-[1fr_auto]"><span className="truncate">{r.name}</span><span className="font-mono text-xs">{dur(r.p95_ms ?? null)}</span></li>))}
              {d?.top_slowest_7d.length === 0 && <li className="px-4 py-3 text-sm text-mute">No completed runs yet.</li>}</ul>
          </div>
          <div>
            <h2 className="mb-2 text-sm font-medium">Most failures, 7 days</h2>
            <ul className="tbl card">{d?.top_failing_7d.map((r) => (
              <li key={r.name} className="row grid-cols-[1fr_auto]"><span className="truncate">{r.name}</span><span className="text-bad">{r.failures}</span></li>))}
              {d?.top_failing_7d.length === 0 && <li className="px-4 py-3 text-sm text-mute">No failures this week.</li>}</ul>
          </div>
        </section>
      </div>
    </Page>
  );
}
