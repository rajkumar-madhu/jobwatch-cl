"use client";
import { useEffect, useState } from "react";
import { Server } from "lucide-react";

/* R61 — Datadog-docs-style helpers for the static docs pages:
   - "Your JobWatch" host selector (like Datadog's site selector): code samples show your own ingest and
     app hosts instead of the example ones; remembered in this browser only.
   - a copy button on every code block. */

const KEY = "jw_docs_hosts";
type Hosts = { ingest: string; app: string };
const clean = (h: string) => h.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");

function load(): Hosts {
  try { return { ingest: "", app: "", ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return { ingest: "", app: "" }; }
}

function apply(h: Hosts) {
  document.querySelectorAll<HTMLElement>(".doc-content code").forEach((el) => {
    const orig = el.dataset.orig ?? (el.dataset.orig = el.textContent ?? "");
    let out = orig;
    if (h.ingest) out = out.replaceAll("ingest.example.com", h.ingest).replaceAll("<ingest>", `https://${h.ingest}`);
    if (h.app) out = out.replaceAll("app.example.com", h.app);
    if (out !== el.textContent) el.textContent = out;
  });
}

export function HostSelector() {
  const [h, setH] = useState<Hosts>({ ingest: "", app: "" });
  const [open, setOpen] = useState(false);
  useEffect(() => { const v = load(); setH(v); apply(v); }, []);
  const save = (v: Hosts) => { setH(v); try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private mode */ } apply(v); };
  const set = !!(h.ingest || h.app);
  return (
    <div className="relative">
      <button className="btn py-1 text-xs" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Server size={13} aria-hidden />{set ? `Samples use ${h.ingest || h.app}` : "Use your JobWatch hosts"}
      </button>
      {open && (
        <div className="overlay absolute right-0 z-20 mt-2 w-80 border border-line bg-panel p-4 text-sm">
          <p className="text-mute">Code samples on every docs page will use your hosts. Saved in this browser only.</p>
          <label className="mt-3 block">Ingest host<input className="input mt-1 font-mono text-xs" placeholder="ingest.example.com" value={h.ingest}
            onChange={(e) => save({ ...h, ingest: clean(e.target.value) })} /></label>
          <label className="mt-3 block">App host (optional)<input className="input mt-1 font-mono text-xs" placeholder="app.example.com" value={h.app}
            onChange={(e) => save({ ...h, app: clean(e.target.value) })} /></label>
          <div className="mt-3 flex justify-between">
            <button className="text-xs text-mute hover:text-ink" onClick={() => save({ ingest: "", app: "" })}>Use the examples</button>
            <button className="btn btn-primary py-1 text-xs" onClick={() => setOpen(false)}>Done</button>
          </div>
        </div>)}
    </div>
  );
}

/** Adds copy buttons to code blocks and applies the saved hosts after each page navigation. */
export function DocEnhancer() {
  const [copied, setCopied] = useState<number | null>(null);
  const [blocks, setBlocks] = useState<HTMLElement[]>([]);
  useEffect(() => {
    apply(load());
    setBlocks(Array.from(document.querySelectorAll<HTMLElement>(".doc-content .code-block")));
  }, []);
  useEffect(() => {
    blocks.forEach((b, i) => {
      if (b.querySelector(".copy-btn")) return;
      const btn = document.createElement("button");
      btn.className = "copy-btn"; btn.type = "button"; btn.setAttribute("aria-label", "Copy code"); btn.textContent = "Copy";
      btn.onclick = async () => {
        try { await navigator.clipboard.writeText(b.querySelector("code")?.textContent ?? ""); } catch { /* clipboard blocked */ }
        btn.textContent = "Copied"; setCopied(i); setTimeout(() => { btn.textContent = "Copy"; setCopied(null); }, 1500);
      };
      b.appendChild(btn);
    });
  }, [blocks]);
  return <span className="sr-only" aria-live="polite">{copied !== null ? "Copied to clipboard" : ""}</span>;
}
