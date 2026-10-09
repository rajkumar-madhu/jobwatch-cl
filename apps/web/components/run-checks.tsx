"use client";
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { mutate } from "@/lib/api";
import { ErrorBox } from "@/components/ui";

/** R54: a job's run checks (assertions). A run that exits 0 but breaks one is recorded as failed. */
export function RunChecks({ jobId, rules, canEdit }: { jobId: string; rules: string[]; canEdit: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(rules.join("\n"));
  useEffect(() => setDraft(rules.join("\n")), [rules]);
  const save = useMutation({
    mutationFn: () => mutate("/api/v1/jobs/{job_id}", "patch", { path: { job_id: jobId },
      body: { assertions: draft.split("\n").map((l) => l.trim()).filter(Boolean) } }),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["job", jobId] }); },
  });
  return (
    <section className="mb-5 rounded-md border border-line px-4 py-3" aria-labelledby="checks-h">
      <div className="flex items-center justify-between gap-3">
        <h2 id="checks-h" className="text-sm font-medium">Run checks</h2>
        {canEdit && !editing && <button className="text-xs text-accent hover:underline" onClick={() => setEditing(true)}>{rules.length ? "Edit" : "Add checks"}</button>}
      </div>
      {!editing ? (rules.length ? (
        <ul className="mt-2 flex flex-wrap gap-2">{rules.map((r) => <li key={r} className="rounded border border-line bg-bg px-2 py-0.5 font-mono text-xs">{r}</li>)}</ul>
      ) : <p className="mt-1 text-xs text-mute">None. A run counts as successful when it exits 0. Add checks to catch runs that succeed but do the wrong thing, such as an empty backup.</p>)
      : (
        <div className="mt-2">
          <textarea className="input h-24 font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Run checks, one per line"
            placeholder={"metric.size_bytes > 0\nduration < 2h"} />
          <p className="mt-1 text-xs text-mute">One per line: <code className="font-mono">metric.&lt;name&gt; &lt;op&gt; &lt;number&gt;</code> or <code className="font-mono">duration &lt;op&gt; &lt;time&gt;</code>. Jobs report metrics in the heartbeat body (<code className="font-mono">"metrics"</code>), Cronitor-style <code className="font-mono">metric=</code> parameters, or by appending <code className="font-mono">name=value</code> lines to <code className="font-mono">$JOBWATCH_METRICS_FILE</code> under <code className="font-mono">cs-run</code>.</p>
          {save.error && <div className="mt-2"><ErrorBox error={save.error} /></div>}
          <div className="mt-2 flex gap-2"><button className="btn btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Save checks</button>
            <button className="btn" onClick={() => { setEditing(false); setDraft(rules.join("\n")); }}>Cancel</button></div>
        </div>
      )}
    </section>
  );
}

export function RunCheckResult({ metrics, failures }: { metrics?: Record<string, number>; failures?: string[] }) {
  const m = Object.entries(metrics ?? {});
  if (!m.length && !failures?.length) return null;
  return (
    <div className="space-y-2 border-b border-line px-4 py-3 text-sm">
      {!!failures?.length && <div className="rounded-md bg-bad/10 px-3 py-2 text-bad" role="status">
        <div className="font-medium">Exited successfully but failed its run checks</div>
        <ul className="mt-1 list-disc pl-5 font-mono text-xs">{failures.map((f) => <li key={f}>{f}</li>)}</ul></div>}
      {!!m.length && <div className="flex flex-wrap gap-2 text-xs"><span className="text-mute">Metrics</span>
        {m.map(([k, v]) => <span key={k} className="rounded border border-line bg-bg px-2 py-0.5 font-mono">{k} = {v.toLocaleString()}</span>)}</div>}
    </div>
  );
}
