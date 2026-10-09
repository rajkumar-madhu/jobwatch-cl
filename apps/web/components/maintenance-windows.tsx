"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mutate } from "@/lib/api";
import { ErrorBox } from "@/components/ui";

type Mw = { id: string; name: string; scope: { job_ids?: string[]; tags?: string[]; environment_ids?: string[] }; starts_at: string; ends_at: string;
  tz: string; rrule: string | null; active_now: boolean; current_end: string | null; next_start: string | null; next_end: string | null };
const DAYS: [string, string][] = [["MO", "Mon"], ["TU", "Tue"], ["WE", "Wed"], ["TH", "Thu"], ["FR", "Fri"], ["SA", "Sat"], ["SU", "Sun"]];
const fmt = (t: string | null, tz?: string) => (t ? new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: tz }) : "—");

function repeatText(m: Mw) {
  if (!m.rrule) return "Once";
  if (m.rrule.startsWith("FREQ=DAILY")) return "Every day";
  const days = /BYDAY=([A-Z,]+)/.exec(m.rrule)?.[1].split(",").map((d) => DAYS.find(([k]) => k === d)?.[1]).join(", ");
  return `Weekly on ${days}`;
}
function scopeText(s: Mw["scope"]) {
  const parts = [s.tags?.length ? `tags ${s.tags.join(", ")}` : "", s.job_ids?.length ? `${s.job_ids.length} job${s.job_ids.length > 1 ? "s" : ""}` : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "All jobs";
}

/** R63: planned quiet periods. Runs inside a window are skipped (no missed, no alert, no SLA hit). */
export function MaintenanceWindows() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["maintenance"], queryFn: () => api<Mw[]>("/api/v1/alerts/maintenance") });
  const tzDefault = typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC";
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", start: "", hours: "2", repeat: "none", days: ["SU"], tz: tzDefault, tags: "" });
  const inv = () => qc.invalidateQueries({ queryKey: ["maintenance"] });
  const create = useMutation({
    mutationFn: () => {
      const start = new Date(f.start);
      const end = new Date(start.getTime() + Number(f.hours) * 3600_000);
      const tags = f.tags.split(",").map((t) => t.trim()).filter(Boolean);
      return mutate("/api/v1/alerts/maintenance", "post", { body: { name: f.name || "Maintenance", starts_at: start.toISOString(), ends_at: end.toISOString(),
        tz: f.tz, repeat: f.repeat, weekdays: f.repeat === "weekly" ? f.days : [], scope: tags.length ? { tags } : {} } as any });
    },
    onSuccess: () => { setOpen(false); setF({ ...f, name: "", start: "", tags: "" }); inv(); },
  });
  const end = useMutation({ onSuccess: inv, mutationFn: (id: string) => mutate("/api/v1/alerts/maintenance/{mw_id}/end", "post", { path: { mw_id: id } }) });
  const del = useMutation({ onSuccess: inv, mutationFn: (id: string) => mutate("/api/v1/alerts/maintenance/{mw_id}", "delete", { path: { mw_id: id } }) });
  const err = q.error || create.error || end.error || del.error;
  return (
    <section className="mb-8" aria-labelledby="mw-h">
      <div className="mb-2 flex items-center justify-between">
        <h2 id="mw-h" className="font-medium">Maintenance windows</h2>
        {!open && <button className="btn" onClick={() => setOpen(true)}>Schedule maintenance</button>}
      </div>
      <p className="mb-3 text-sm text-mute">Runs due inside a window are skipped: no missed runs, no alerts, nothing counted against reliability.
        Public status pages show upcoming windows for the jobs they list.</p>
      {err && <ErrorBox error={err} />}
      {open && (
        <div className="card mb-3 grid gap-3 p-4 text-sm sm:grid-cols-2">
          <label>Name<input className="input mt-1" value={f.name} placeholder="Database patching" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label>Starts<input className="input mt-1" type="datetime-local" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} /></label>
          <label>Lasts (hours)<input className="input mt-1" type="number" min="0.25" max="336" step="0.25" value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })} /></label>
          <label>Time zone<input className="input mt-1 font-mono text-xs" value={f.tz} onChange={(e) => setF({ ...f, tz: e.target.value })} /></label>
          <label>Repeats<select className="input mt-1" value={f.repeat} onChange={(e) => setF({ ...f, repeat: e.target.value })}>
            <option value="none">Once</option><option value="daily">Every day</option><option value="weekly">Every week</option></select></label>
          <label>Only jobs tagged (optional)<input className="input mt-1" value={f.tags} placeholder="db, payments — empty for all jobs" onChange={(e) => setF({ ...f, tags: e.target.value })} /></label>
          {f.repeat === "weekly" && (
            <fieldset className="sm:col-span-2"><legend className="mb-1">On</legend>
              <div className="flex flex-wrap gap-2">{DAYS.map(([k, l]) => (
                <label key={k} className={`cursor-pointer rounded-md border px-2.5 py-1 ${f.days.includes(k) ? "border-accent bg-accent/10 text-accent" : "border-line"}`}>
                  <input type="checkbox" className="sr-only" checked={f.days.includes(k)}
                    onChange={() => setF({ ...f, days: f.days.includes(k) ? f.days.filter((d) => d !== k) : [...f.days, k] })} />{l}</label>))}</div>
            </fieldset>)}
          <div className="flex gap-2 sm:col-span-2">
            <button className="btn btn-primary" disabled={!f.start || create.isPending} onClick={() => create.mutate()}>Save window</button>
            <button className="btn" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>)}
      {q.data && !q.data.length && !open && <p className="text-sm text-mute">No maintenance scheduled.</p>}
      {!!q.data?.length && (
        <div className="card divide-y divide-line">{q.data.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
            <div className="min-w-0">
              <div className="flex items-center gap-2"><span className="font-medium">{m.name}</span>
                {m.active_now && <span className="rounded border border-accent px-1.5 py-0.5 text-[11px] font-medium text-accent">Active now</span>}</div>
              <div className="mt-0.5 text-xs text-mute">{repeatText(m)} · {scopeText(m.scope)} · {m.tz}</div>
              <div className="mt-0.5 text-xs">{m.active_now ? `Until ${fmt(m.current_end)}` : m.next_start ? `Next: ${fmt(m.next_start)} → ${fmt(m.next_end)}` : "Finished"}</div>
            </div>
            <div className="flex gap-3 text-xs">
              {m.active_now && !m.rrule && <button className="text-accent hover:underline" onClick={() => end.mutate(m.id)}>End now</button>}
              <button className="text-mute hover:text-bad" onClick={() => { if (confirm(`Delete "${m.name}"?`)) del.mutate(m.id); }}>Delete</button>
            </div>
          </div>))}</div>)}
    </section>
  );
}
