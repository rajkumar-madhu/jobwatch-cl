"use client";
import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, mutate } from "@/lib/api";
import { Page, ErrorBox } from "@/components/ui";

type Result = { name: string; status: string; note: string | null; job_id: string | null; schedule_expr: string | null; kept_ping_url: boolean; new_ping_url: string | null };
type Out = { dry_run: boolean; results: Result[]; ping_host_change: string };

const BADGE: Record<string, string> = {
  imported: "bg-ok/15 text-ok", converted: "bg-warn/15 text-warn", needs_schedule: "bg-warn/15 text-warn",
  skipped: "bg-mute/15 text-mute", exists: "bg-mute/15 text-mute",
};
const LABEL: Record<string, string> = { imported: "Ready", converted: "Schedule converted", needs_schedule: "Needs a schedule", skipped: "Not imported", exists: "Already here" };

type Source = "healthchecks" | "cronitor";
const SOURCES: Record<Source, { label: string; base: string; keyHelp: string }> = {
  healthchecks: { label: "Healthchecks.io", base: "https://healthchecks.io",
    keyHelp: "Project → Settings → API Access. Use the read-write key to keep ping URLs; a read-only key works but every job gets a new URL." },
  cronitor: { label: "Cronitor", base: "https://cronitor.io",
    keyHelp: "Settings → API keys: a key with the monitor:read scope. Not stored: used for this import only." },
};

/** R48 Healthchecks, R50 Cronitor. Preview first, then import. */
export default function ImportPage() {
  const ws = useQuery({ queryKey: ["workspaces"], queryFn: () => api<{ id: string; name: string }[]>("/api/v1/workspaces") });
  const [src, setSrc] = useState<Source>("healthchecks");
  const [f, setF] = useState({ base_url: SOURCES.healthchecks.base, api_key: "", telemetry_key: "", workspace_id: "" });
  const [out, setOut] = useState<Out | null>(null);
  const pick = (s: Source) => { setSrc(s); setOut(null); setF({ ...f, base_url: SOURCES[s].base, api_key: "", telemetry_key: "" }); };
  const run = useMutation({
    mutationFn: (dry_run: boolean) => {
      const body: any = { base_url: f.base_url, api_key: f.api_key, workspace_id: f.workspace_id || ws.data?.[0]?.id, dry_run };
      if (src === "cronitor") {
        if (f.telemetry_key) body.telemetry_key = f.telemetry_key;
        return mutate("/api/v1/import/cronitor", "post", { body });
      }
      return mutate("/api/v1/import/healthchecks", "post", { body });
    },
    onSuccess: (r: any) => setOut(r),
  });
  const importable = out?.results.filter((r) => !["skipped", "exists"].includes(r.status)).length ?? 0;
  return (
    <Page title="Import jobs" subtitle="Bring your monitors over and keep your existing ping URLs: only the hostname changes.">
      <div className="mb-4 flex gap-2" role="tablist" aria-label="Import from">
        {(Object.keys(SOURCES) as Source[]).map((s) => (
          <button key={s} role="tab" aria-selected={src === s} className={`btn ${src === s ? "border-accent text-accent" : ""}`} onClick={() => pick(s)}>{SOURCES[s].label}</button>))}
      </div>
      <div className="card max-w-2xl space-y-3 p-4">
        <label className="block text-sm">{SOURCES[src].label} URL<input className="input mt-1 font-mono" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} />
          {src === "healthchecks" && <span className="mt-1 block text-xs text-mute">https://healthchecks.io, or your self-hosted instance.</span>}</label>
        <label className="block text-sm">API key<input className="input mt-1 font-mono" type="password" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} />
          <span className="mt-1 block text-xs text-mute">{SOURCES[src].keyHelp} The key is used for this import only and is not stored.</span></label>
        {src === "cronitor" && <label className="block text-sm">Telemetry key (recommended)<input className="input mt-1 font-mono" type="password" value={f.telemetry_key} onChange={(e) => setF({ ...f, telemetry_key: e.target.value })} />
          <span className="mt-1 block text-xs text-mute">The key inside your cronitor.link URLs. With it, those URLs keep working with only the host changed; without it, every job gets a new URL.</span></label>}
        <label className="block text-sm">Into workspace<select className="input mt-1" value={f.workspace_id} onChange={(e) => setF({ ...f, workspace_id: e.target.value })}>
          {ws.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
        <div className="flex gap-2 pt-1">
          <button className="btn" disabled={!f.api_key || run.isPending} onClick={() => run.mutate(true)}>Preview</button>
          {out?.dry_run && importable > 0 && <button className="btn btn-primary" disabled={run.isPending} onClick={() => run.mutate(false)}>Import {importable} job{importable === 1 ? "" : "s"}</button>}
        </div>
      </div>
      {run.error && <div className="mt-4"><ErrorBox error={run.error} /></div>}
      {out && <div className="mt-6 space-y-4">
        {!out.dry_run && <div className="rounded-md border border-accent/50 bg-accent/[.07] px-4 py-3 text-sm" role="status">
          <div className="font-medium">Imported. One change left in your scripts:</div><p className="mt-1">{out.ping_host_change}</p>
          {out.results.some((r) => r.new_ping_url) && <p className="mt-1 text-mute">Jobs with a new ping URL are listed below. Copy those now: they are shown only once.</p>}
          <Link href="/jobs" className="mt-2 inline-block underline">Go to jobs</Link></div>}
        <div className="card overflow-x-auto"><table className="tbl w-full text-sm">
          <thead><tr className="text-left text-xs text-mute"><th className="px-4 py-2 font-normal">Check</th><th className="px-4 py-2 font-normal">Result</th><th className="px-4 py-2 font-normal">Schedule</th><th className="px-4 py-2 font-normal">Ping URL</th></tr></thead>
          <tbody>{out.results.map((r) => (
            <tr key={r.name} className="border-t border-line align-top">
              <td className="px-4 py-2"><div className="font-medium">{r.name}</div>{r.note && <div className="text-xs text-mute">{r.note}</div>}</td>
              <td className="px-4 py-2"><span className={`rounded-full px-2 py-0.5 text-xs ${BADGE[r.status] ?? ""}`}>{LABEL[r.status] ?? r.status}</span></td>
              <td className="px-4 py-2 font-mono text-xs">{r.schedule_expr ?? "—"}</td>
              <td className="px-4 py-2 text-xs">{r.kept_ping_url ? (src === "cronitor" ? "Unchanged path under /cronitor/" : "Unchanged path under /hc/") : r.new_ping_url && r.job_id ? <code className="break-all font-mono">{r.new_ping_url}</code> : r.new_ping_url ? "New URL on import" : "—"}</td>
            </tr>))}</tbody></table></div>
      </div>}
    </Page>
  );
}
