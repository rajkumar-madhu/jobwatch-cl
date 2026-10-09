"use client";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ErrorBox, Page } from "@/components/ui";

type Person = { id: string; name: string; email: string };
type Shift = { start: string; end: string; user_id: string | null; name: string | null; source: string };
type Sched = { id: string; name: string; tz: string; on_call_now: { user_id: string | null; name: string | null; source: string };
  layers: { name: string; rotation: string; shift_hours: number | null; members: { id: string; name: string }[] }[]; timeline?: Shift[];
  overrides?: { id: string; user_id: string; starts_at: string; ends_at: string }[] };
type Policy = { id: string; name: string; repeat_count: number; delivery_channel_id: string; steps: { delay_min: number; targets: { type: string; id: string }[] }[] };
type Run = { id: string; status: string; step: number; cycle: number; next_at: string; job_name: string | null; condition: string | null; policy: string;
  history: { at: string; step: number; paged: string[]; notes: string[] }[]; created_at: string };

const when = (t: string, tz?: string) => new Date(t).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: tz });
const post = (path: string, body?: unknown) => api<any>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

function ActivePages({ ackId }: { ackId: string | null }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["escalations"], queryFn: () => api<Run[]>("/api/v1/oncall/escalations"), refetchInterval: 15000 });
  const ack = useMutation({ mutationFn: (id: string) => post(`/api/v1/oncall/escalations/${id}/ack`), onSuccess: () => qc.invalidateQueries({ queryKey: ["escalations"] }) });
  if (!q.data?.length) return null;
  return (
    <section className="mb-8" aria-labelledby="pages-h">
      <h2 id="pages-h" className="mb-2 font-medium">Paging now</h2>
      {ack.error && <ErrorBox error={ack.error} />}
      <div className="card divide-y divide-line">{q.data.map((r) => {
        const last = r.history[r.history.length - 1];
        return (
          <div key={r.id} className={`flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm ${r.id === ackId ? "bg-accent/10" : ""}`}>
            <div className="min-w-0">
              <div className="font-medium">{r.job_name ?? "Alert"} <span className="text-mute">· {r.condition}</span></div>
              <div className="text-xs text-mute">{r.policy} · step {last?.step ?? 1}{last?.paged.length ? ` paged ${last.paged.join(", ")}` : ""}{last?.notes.length ? ` · ${last.notes.join("; ")}` : ""}</div>
              <div className="text-xs">Escalates {when(r.next_at)} unless acknowledged</div>
            </div>
            <button className="btn btn-primary" disabled={ack.isPending} onClick={() => ack.mutate(r.id)}>Acknowledge</button>
          </div>);
      })}</div>
    </section>
  );
}

function ScheduleCard({ id, people }: { id: string; people: Person[] }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["schedule", id], queryFn: () => api<Sched>(`/api/v1/oncall/schedules/${id}?days=14`) });
  const [ov, setOv] = useState({ open: false, user: "", from: "", until: "" });
  const add = useMutation({ mutationFn: () => post(`/api/v1/oncall/schedules/${id}/overrides`, { user_id: ov.user, starts_at: new Date(ov.from).toISOString(), ends_at: new Date(ov.until).toISOString() }),
    onSuccess: () => { setOv({ open: false, user: "", from: "", until: "" }); qc.invalidateQueries({ queryKey: ["schedule", id] }); qc.invalidateQueries({ queryKey: ["schedules"] }); } });
  const s = q.data;
  if (!s) return <div className="card p-4 text-sm text-mute">Loading…</div>;
  return (
    <div className="card p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div><div className="font-medium">{s.name}</div><div className="text-xs text-mute">{s.tz} · {s.layers.map((l) => `${l.name}: ${l.rotation === "custom" ? `every ${l.shift_hours} h` : l.rotation === "daily" ? "every day" : "every week"}, ${l.members.map((m) => m.name).join(" → ")}`).join(" | ")}</div></div>
        <div className="text-right"><div className="text-xs text-mute">On call now</div>
          <div className="font-medium">{s.on_call_now.name ?? "Nobody"}{s.on_call_now.source === "override" && <span className="ml-1 text-xs text-accent">(override)</span>}</div></div>
      </div>
      <table className="mt-3 w-full text-xs"><caption className="sr-only">Upcoming shifts for {s.name}</caption>
        <tbody>{(s.timeline ?? []).slice(0, 6).map((x, i) => (
          <tr key={i} className="border-t border-line"><td className="py-1.5 pr-3 text-mute">{when(x.start, s.tz)} → {when(x.end, s.tz)}</td>
            <td className="py-1.5 font-medium">{x.name ?? "Nobody"}</td><td className="py-1.5 text-right text-mute">{x.source === "override" ? "override" : ""}</td></tr>))}</tbody></table>
      {add.error && <div className="mt-2"><ErrorBox error={add.error} /></div>}
      {!ov.open ? <button className="mt-3 text-xs text-accent hover:underline" onClick={() => setOv({ ...ov, open: true })}>Add override</button> : (
        <div className="mt-3 grid gap-2 sm:grid-cols-4">
          <label className="text-xs">Who<select className="input mt-1" value={ov.user} onChange={(e) => setOv({ ...ov, user: e.target.value })}>
            <option value="">Choose…</option>{people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label className="text-xs">From<input className="input mt-1" type="datetime-local" value={ov.from} onChange={(e) => setOv({ ...ov, from: e.target.value })} /></label>
          <label className="text-xs">Until<input className="input mt-1" type="datetime-local" value={ov.until} onChange={(e) => setOv({ ...ov, until: e.target.value })} /></label>
          <div className="flex items-end gap-2"><button className="btn btn-primary" disabled={!ov.user || !ov.from || !ov.until || add.isPending} onClick={() => add.mutate()}>Save</button>
            <button className="btn" onClick={() => setOv({ ...ov, open: false })}>Cancel</button></div>
        </div>)}
    </div>
  );
}

function NewSchedule({ people, onDone }: { people: Person[]; onDone: () => void }) {
  const tz = typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC";
  const [f, setF] = useState({ name: "", tz, rotation: "weekly", hours: "12", handoff: "", members: [] as string[] });
  const create = useMutation({ onSuccess: onDone, mutationFn: () => post("/api/v1/oncall/schedules", { name: f.name, tz: f.tz, layers: [{ name: "Rotation", rotation: f.rotation,
    shift_hours: f.rotation === "custom" ? Number(f.hours) : null, handoff_at: new Date(f.handoff).toISOString(), members: f.members }] }) });
  return (
    <div className="card mb-3 grid gap-3 p-4 text-sm sm:grid-cols-2">
      {create.error && <div className="sm:col-span-2"><ErrorBox error={create.error} /></div>}
      <label>Name<input className="input mt-1" value={f.name} placeholder="Platform primary" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label>Time zone<input className="input mt-1 font-mono text-xs" value={f.tz} onChange={(e) => setF({ ...f, tz: e.target.value })} /></label>
      <label>Rotates<select className="input mt-1" value={f.rotation} onChange={(e) => setF({ ...f, rotation: e.target.value })}>
        <option value="daily">Every day</option><option value="weekly">Every week</option><option value="custom">Every N hours</option></select></label>
      {f.rotation === "custom" ? <label>Shift length (hours)<input className="input mt-1" type="number" min="1" max="720" value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })} /></label>
        : <label>First handoff<input className="input mt-1" type="datetime-local" value={f.handoff} onChange={(e) => setF({ ...f, handoff: e.target.value })} /></label>}
      {f.rotation === "custom" && <label>First handoff<input className="input mt-1" type="datetime-local" value={f.handoff} onChange={(e) => setF({ ...f, handoff: e.target.value })} /></label>}
      <fieldset className="sm:col-span-2"><legend className="mb-1">People, in rotation order</legend>
        <div className="flex flex-wrap gap-2">{people.map((p) => { const i = f.members.indexOf(p.id); return (
          <label key={p.id} className={`cursor-pointer rounded-md border px-2.5 py-1 ${i >= 0 ? "border-accent bg-accent/10 text-accent" : "border-line"}`}>
            <input type="checkbox" className="sr-only" checked={i >= 0} onChange={() => setF({ ...f, members: i >= 0 ? f.members.filter((x) => x !== p.id) : [...f.members, p.id] })} />
            {i >= 0 ? `${i + 1}. ` : ""}{p.name}</label>); })}</div></fieldset>
      <div className="flex gap-2 sm:col-span-2"><button className="btn btn-primary" disabled={!f.name || !f.handoff || !f.members.length || create.isPending} onClick={() => create.mutate()}>Create schedule</button>
        <button className="btn" onClick={onDone}>Cancel</button></div>
    </div>
  );
}

function Policies({ schedules, people }: { schedules: Sched[]; people: Person[] }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["policies"], queryFn: () => api<Policy[]>("/api/v1/oncall/policies") });
  const channels = useQuery({ queryKey: ["channels"], queryFn: () => api<any[]>("/api/v1/alerts/channels") });
  const deliverable = (channels.data ?? []).filter((c) => ["email", "slack", "teams", "discord", "telegram", "webhook"].includes(c.kind));
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", channel: "", repeat: "1", steps: [{ target: "", delay: "15" }, { target: "", delay: "15" }] });
  const [routed, setRouted] = useState<Record<string, string>>({});
  const nameOf = (t: { type: string; id: string }) => t.type === "schedule" ? `${schedules.find((x) => x.id === t.id)?.name ?? "schedule"} (on call)` : people.find((x) => x.id === t.id)?.name ?? "person";
  const create = useMutation({ mutationFn: () => post("/api/v1/oncall/policies", { name: f.name, repeat_count: Number(f.repeat), delivery_channel_id: f.channel,
      steps: f.steps.filter((x) => x.target).map((x) => { const [type, id] = x.target.split(":"); return { delay_min: Number(x.delay), targets: [{ type, id }] }; }) }),
    onSuccess: () => { setOpen(false); qc.invalidateQueries({ queryKey: ["policies"] }); } });
  const route = useMutation({ mutationFn: (id: string) => post(`/api/v1/oncall/policies/${id}/channel`),
    onSuccess: (r: any, id) => { setRouted({ ...routed, [id]: r?.name }); qc.invalidateQueries({ queryKey: ["channels"] }); } });
  const err = q.error || create.error || route.error;
  return (
    <section aria-labelledby="pol-h">
      <div className="mb-2 flex items-center justify-between"><h2 id="pol-h" className="font-medium">Escalation policies</h2>
        {!open && <button className="btn" onClick={() => setOpen(true)}>New policy</button>}</div>
      <p className="mb-3 text-sm text-mute">Who is paged, in what order, and how long to wait for an acknowledgement before paging the next step.</p>
      {err && <ErrorBox error={err} />}
      {open && (
        <div className="card mb-3 grid gap-3 p-4 text-sm sm:grid-cols-3">
          <label>Name<input className="input mt-1" value={f.name} placeholder="Platform" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label>Deliver pages through<select className="input mt-1" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>
            <option value="">Choose a channel…</option>{deliverable.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.kind})</option>)}</select></label>
          <label>Then repeat<select className="input mt-1" value={f.repeat} onChange={(e) => setF({ ...f, repeat: e.target.value })}>
            {[0, 1, 2, 3].map((n) => <option key={n} value={n}>{n === 0 ? "No repeat" : `${n} more time${n > 1 ? "s" : ""}`}</option>)}</select></label>
          {f.steps.map((st, i) => (
            <div key={i} className="grid gap-2 sm:col-span-3 sm:grid-cols-[1fr_200px]">
              <label>Step {i + 1}: page<select className="input mt-1" value={st.target} onChange={(e) => setF({ ...f, steps: f.steps.map((x, j) => j === i ? { ...x, target: e.target.value } : x) })}>
                <option value="">{i === 0 ? "Choose…" : "No further step"}</option>
                <optgroup label="Whoever is on call in">{schedules.map((s) => <option key={s.id} value={`schedule:${s.id}`}>{s.name}</option>)}</optgroup>
                <optgroup label="A person">{people.map((p) => <option key={p.id} value={`user:${p.id}`}>{p.name}</option>)}</optgroup></select></label>
              <label>then wait (minutes)<input className="input mt-1" type="number" min="1" max="1440" value={st.delay} onChange={(e) => setF({ ...f, steps: f.steps.map((x, j) => j === i ? { ...x, delay: e.target.value } : x) })} /></label>
            </div>))}
          <div className="flex gap-2 sm:col-span-3"><button className="btn btn-primary" disabled={!f.name || !f.channel || !f.steps[0].target || create.isPending} onClick={() => create.mutate()}>Create policy</button>
            <button className="btn" onClick={() => setOpen(false)}>Cancel</button></div>
        </div>)}
      {q.data && !q.data.length && !open && <p className="text-sm text-mute">No escalation policies yet.</p>}
      {!!q.data?.length && <div className="card divide-y divide-line">{q.data.map((p) => (
        <div key={p.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
          <div className="min-w-0"><div className="font-medium">{p.name}</div>
            <ol className="mt-1 text-xs text-mute">{p.steps.map((s, i) => <li key={i}>{i + 1}. {s.targets.map(nameOf).join(", ")}, then wait {s.delay_min} min</li>)}</ol>
            {p.repeat_count > 0 && <div className="text-xs text-mute">Repeats {p.repeat_count} more time{p.repeat_count > 1 ? "s" : ""}</div>}</div>
          {routed[p.id] ? <span className="text-xs text-ok">Alert rules can now use “{routed[p.id]}”</span>
            : <button className="btn" onClick={() => route.mutate(p.id)}>Route alerts here</button>}
        </div>))}</div>}
    </section>
  );
}

export default function OnCallPage() {
  const qc = useQueryClient();
  const [ackId, setAckId] = useState<string | null>(null);
  useEffect(() => { setAckId(new URLSearchParams(window.location.search).get("ack")); }, []);
  const people = useQuery({ queryKey: ["oncall-people"], queryFn: () => api<Person[]>("/api/v1/oncall/people") });
  const schedules = useQuery({ queryKey: ["schedules"], queryFn: () => api<Sched[]>("/api/v1/oncall/schedules") });
  const [adding, setAdding] = useState(false);
  return (
    <Page title="On-call">
      <ActivePages ackId={ackId} />
      <section className="mb-8" aria-labelledby="sch-h">
        <div className="mb-2 flex items-center justify-between"><h2 id="sch-h" className="font-medium">Schedules</h2>
          {!adding && <button className="btn" onClick={() => setAdding(true)}>New schedule</button>}</div>
        {schedules.error && <ErrorBox error={schedules.error} />}
        {adding && <NewSchedule people={people.data ?? []} onDone={() => { setAdding(false); qc.invalidateQueries({ queryKey: ["schedules"] }); }} />}
        {schedules.data && !schedules.data.length && !adding && <p className="text-sm text-mute">No schedules yet. Create one, then an escalation policy that pages it.</p>}
        <div className="grid gap-3 lg:grid-cols-2">{schedules.data?.map((s) => <ScheduleCard key={s.id} id={s.id} people={people.data ?? []} />)}</div>
      </section>
      <Policies schedules={schedules.data ?? []} people={people.data ?? []} />
    </Page>
  );
}
