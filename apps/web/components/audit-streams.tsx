"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mutate } from "@/lib/api";
import { ErrorBox } from "@/components/ui";

type Stream = { id: string; kind: "webhook" | "splunk_hec" | "datadog_logs"; name: string; host: string; enabled: boolean; delivered_total: number; pending: number;
  last_success_at: string | null; last_error: string | null; last_error_at: string | null; next_attempt_at: string };
const KIND = { webhook: "Signed webhook", splunk_hec: "Splunk HEC", datadog_logs: "Datadog Logs" } as const;
export const DD_SITES: [string, string][] = [["datadoghq.com", "US1"], ["us3.datadoghq.com", "US3"], ["us5.datadoghq.com", "US5"], ["datadoghq.eu", "EU1"],
  ["ap1.datadoghq.com", "AP1 (Japan)"], ["ap2.datadoghq.com", "AP2 (Australia)"], ["uk1.datadoghq.com", "UK1"], ["ddog-gov.com", "US1-FED"], ["us2.ddog-gov.com", "US2-FED"]];
const when = (t: string | null) => (t ? new Date(t).toLocaleString() : "never");

/** R57: stream the audit log to a SIEM. Owners only (the API enforces it too). */
export function AuditStreams({ role }: { role?: string }) {
  const qc = useQueryClient();
  const isOwner = role === "owner";
  const q = useQuery({ queryKey: ["audit-streams"], queryFn: () => api<Stream[]>("/api/v1/audit-streams"), enabled: isOwner, refetchInterval: 15000 });
  const [f, setF] = useState({ kind: "webhook", name: "", url: "", secret: "", token: "", index: "", start: "now", api_key: "", site: "datadoghq.com" });
  const [adding, setAdding] = useState(false);
  const [tested, setTested] = useState<Record<string, string>>({});
  const inv = () => qc.invalidateQueries({ queryKey: ["audit-streams"] });
  const add = useMutation({ onSuccess: () => { setAdding(false); setF({ ...f, name: "", url: "", secret: "", token: "", index: "" }); inv(); },
    mutationFn: () => mutate("/api/v1/audit-streams", "post", { body: {
      kind: f.kind, name: f.name, url: f.url, start: f.start,
      ...(f.kind === "webhook" ? { secret: f.secret } : f.kind === "datadog_logs" ? { api_key: f.api_key, site: f.site, url: null } : { token: f.token, index: f.index || null }) } as any }) });
  const toggle = useMutation({ onSuccess: inv, mutationFn: (s: Stream) => mutate("/api/v1/audit-streams/{stream_id}", "patch", { path: { stream_id: s.id }, body: { enabled: !s.enabled } }) });
  const del = useMutation({ onSuccess: inv, mutationFn: (id: string) => mutate("/api/v1/audit-streams/{stream_id}", "delete", { path: { stream_id: id } }) });
  const test = useMutation({ mutationFn: (id: string) => mutate("/api/v1/audit-streams/{stream_id}/test", "post", { path: { stream_id: id } }),
    onSuccess: (r: any, id) => setTested({ ...tested, [id]: r?.ok ? "Test event delivered" : `Test failed: ${r?.error}` }) });

  if (!isOwner) return <p className="text-mute">Only owners can stream the audit log to a SIEM.</p>;
  const err = q.error || add.error || toggle.error || del.error || test.error;
  return (
    <div className="space-y-3">
      <p className="text-mute">Send every audit-log event (sign-ins, role changes, configuration changes, deletions) to your SIEM as it happens.
        Delivery is in order and at least once; if your SIEM is down, events wait and catch up. Each event carries its id for de-duplication.</p>
      {err && <ErrorBox error={err} />}
      {q.data?.map((s) => (
        <div key={s.id} className="card p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span><span className="font-medium">{s.name}</span> <span className="text-xs text-mute">{KIND[s.kind]} · {s.host}</span></span>
            <span className={`text-xs ${!s.enabled ? "text-mute" : s.last_error ? "text-bad" : "text-ok"}`}>{!s.enabled ? "Paused" : s.last_error ? "Failing" : "Delivering"}</span>
          </div>
          <div className="mt-2 grid gap-x-6 gap-y-1 text-xs text-mute sm:grid-cols-3">
            <span>{s.delivered_total.toLocaleString()} delivered</span><span>{s.pending.toLocaleString()} waiting</span><span>last success {when(s.last_success_at)}</span>
          </div>
          {s.last_error && <p className="mt-2 text-xs text-bad">{s.last_error} ({when(s.last_error_at)}); retrying {when(s.next_attempt_at)}</p>}
          {tested[s.id] && <p className="mt-2 text-xs" role="status">{tested[s.id]}</p>}
          <div className="mt-3 flex gap-3 text-xs">
            <button className="text-accent hover:underline" onClick={() => test.mutate(s.id)}>Send test event</button>
            <button className="text-accent hover:underline" onClick={() => toggle.mutate(s)}>{s.enabled ? "Pause" : "Resume"}</button>
            <button className="text-mute hover:text-bad" onClick={() => { if (confirm(`Stop streaming to ${s.name}?`)) del.mutate(s.id); }}>Remove</button>
          </div>
        </div>))}
      {!adding ? <button className="btn" onClick={() => setAdding(true)}>Add destination</button> : (
        <div className="card space-y-3 p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">Type<select className="input mt-1" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              <option value="webhook">Signed webhook (Logstash, Vector, Fluent Bit…)</option><option value="splunk_hec">Splunk HTTP Event Collector</option>
              <option value="datadog_logs">Datadog Logs</option></select></label>
            <label className="text-sm">Name<input className="input mt-1" value={f.name} placeholder="Security SIEM" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
            {f.kind === "datadog_logs" ? <>
              <label className="text-sm">Datadog API key<input className="input mt-1 font-mono" type="password" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} /></label>
              <label className="text-sm">Site<select className="input mt-1" value={f.site} onChange={(e) => setF({ ...f, site: e.target.value })}>
                {DD_SITES.map(([v, l]) => <option key={v} value={v}>{l} · {v}</option>)}</select></label>
            </> : <>
            <label className="text-sm sm:col-span-2">URL<input className="input mt-1 font-mono" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })}
              placeholder={f.kind === "webhook" ? "https://logs.example.com/jobwatch" : "https://splunk.example.com:8088/services/collector/event"} /></label>
            {f.kind === "webhook"
              ? <label className="text-sm sm:col-span-2">Signing secret (16+ characters)<input className="input mt-1 font-mono" type="password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />
                  <span className="mt-1 block text-xs text-mute">Verify X-JobWatch-Signature: sha256=HMAC(secret, body) on the receiving side.</span></label>
              : <><label className="text-sm">HEC token<input className="input mt-1 font-mono" type="password" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} /></label>
                  <label className="text-sm">Index (optional)<input className="input mt-1" value={f.index} onChange={(e) => setF({ ...f, index: e.target.value })} /></label></>}
            </>}
            <label className="text-sm">Start from<select className="input mt-1" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })}>
              <option value="now">New events only</option><option value="30d">Include the last 30 days</option></select></label>
          </div>
          <div className="flex gap-2"><button className="btn btn-primary" disabled={add.isPending || !f.name || (f.kind === "datadog_logs" ? !f.api_key : !f.url)} onClick={() => add.mutate()}>Start streaming</button>
            <button className="btn" onClick={() => setAdding(false)}>Cancel</button></div>
        </div>)}
    </div>
  );
}
