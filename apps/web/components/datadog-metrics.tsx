"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mutate } from "@/lib/api";
import { ErrorBox } from "@/components/ui";
import { DD_SITES } from "@/components/audit-streams";

type Export = { id: string; name: string; site: string; enabled: boolean; points_total: number; last_success_at: string | null;
  last_error: string | null; last_error_at: string | null; next_attempt_at: string };
const METRICS: [string, string][] = [
  ["jobwatch.run.duration", "seconds per finished run, tagged status"], ["jobwatch.run.completed", "count of finished runs, tagged status"],
  ["jobwatch.run.missed", "count of expected runs that never started"], ["jobwatch.job.failing", "1 while a job is failing, missed or timed out"],
  ["jobwatch.job.reliability", "reliability score, 0–100"],
];

/** R58: send job metrics to Datadog every minute (tags: job, env, team, host, plus your own). */
export function DatadogMetrics() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["metric-exports"], queryFn: () => api<Export[]>("/api/v1/metric-exports"), retry: false, refetchInterval: 30000 });
  const [f, setF] = useState({ api_key: "", site: "datadoghq.com", tags: "" });
  const [msg, setMsg] = useState<Record<string, string>>({});
  const inv = () => qc.invalidateQueries({ queryKey: ["metric-exports"] });
  const add = useMutation({ onSuccess: () => { setF({ ...f, api_key: "", tags: "" }); inv(); },
    mutationFn: () => mutate("/api/v1/metric-exports", "post", { body: { name: "Datadog", api_key: f.api_key, site: f.site, tags: f.tags || null } }) });
  const toggle = useMutation({ onSuccess: inv, mutationFn: (x: Export) => mutate("/api/v1/metric-exports/{export_id}", "patch", { path: { export_id: x.id }, body: { enabled: !x.enabled } }) });
  const del = useMutation({ onSuccess: inv, mutationFn: (id: string) => mutate("/api/v1/metric-exports/{export_id}", "delete", { path: { export_id: id } }) });
  const test = useMutation({ mutationFn: (id: string) => mutate("/api/v1/metric-exports/{export_id}/test", "post", { path: { export_id: id } }),
    onSuccess: (r: any, id) => setMsg({ ...msg, [id]: r?.ok ? "API key accepted by Datadog" : `Check failed: ${r?.error}` }) });
  const err = add.error || toggle.error || del.error || test.error;
  return (
    <section className="card p-4" aria-labelledby="dd-h">
      <h2 id="dd-h" className="font-medium">Datadog metrics</h2>
      <p className="mt-1 text-sm text-mute">Every minute, JobWatch sends job metrics to Datadog for dashboards and monitors. Alerts can also go to Datadog
        as events (Alerting → Add channel → Datadog) and the audit log to Datadog Logs (Settings → Audit log streaming).</p>
      <ul className="mt-3 grid gap-1 text-xs sm:grid-cols-2">{METRICS.map(([m, d]) => <li key={m}><code className="font-mono">{m}</code> <span className="text-mute">{d}</span></li>)}</ul>
      {q.error && <p className="mt-3 text-xs text-mute">Admins can set this up; metrics export is on the Team plan and above.</p>}
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
      {q.data?.map((x) => (
        <div key={x.id} className="mt-3 rounded-md border border-line px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2"><span>Datadog <span className="text-xs text-mute">{x.site}</span></span>
            <span className={`text-xs ${!x.enabled ? "text-mute" : x.last_error ? "text-bad" : "text-ok"}`}>{!x.enabled ? "Paused" : x.last_error ? "Failing" : "Sending"}</span></div>
          <div className="mt-1 text-xs text-mute">{x.points_total.toLocaleString()} points sent · last success {x.last_success_at ? new Date(x.last_success_at).toLocaleString() : "not yet"}</div>
          {x.last_error && <p className="mt-1 text-xs text-bad">{x.last_error}; retrying {new Date(x.next_attempt_at).toLocaleString()}</p>}
          {msg[x.id] && <p className="mt-1 text-xs" role="status">{msg[x.id]}</p>}
          <div className="mt-2 flex gap-3 text-xs"><button className="text-accent hover:underline" onClick={() => test.mutate(x.id)}>Check API key</button>
            <button className="text-accent hover:underline" onClick={() => toggle.mutate(x)}>{x.enabled ? "Pause" : "Resume"}</button>
            <button className="text-mute hover:text-bad" onClick={() => { if (confirm("Stop sending metrics to Datadog?")) del.mutate(x.id); }}>Remove</button></div>
        </div>))}
      {q.data && !q.data.length && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <label className="text-sm">Datadog API key<input className="input mt-1 font-mono" type="password" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></label>
          <label className="text-sm">Site<select className="input mt-1" value={f.site} onChange={(e) => setF({ ...f, site: e.target.value })}>
            {DD_SITES.map(([v, l]) => <option key={v} value={v}>{l} · {v}</option>)}</select></label>
          <label className="text-sm">Extra tags (optional)<input className="input mt-1 font-mono" value={f.tags} placeholder="team:platform" onChange={(e) => setF({ ...f, tags: e.target.value })} /></label>
          <div className="sm:col-span-3"><button className="btn btn-primary" disabled={!f.api_key || add.isPending} onClick={() => add.mutate()}>Start sending metrics</button></div>
        </div>)}
    </section>
  );
}
