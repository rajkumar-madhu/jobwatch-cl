/* R59 — the public docs site, generated at build time from the repository's docs/*.md.
   The Markdown files stay the single source of truth; pages are statically rendered, so the running
   web container needs no access to them. Internal notes (development log, design doc, rename notes)
   are deliberately not listed here. */
import "server-only";
import fs from "node:fs";
import path from "node:path";
import { Marked } from "marked";

export type DocMeta = { slug: string; file: string; title: string; group: string; summary: string; related?: string[] };
export const GROUPS = ["Get started", "Alerts and checks", "Security and identity", "Integrations", "Self-hosting", "Reference"] as const;

const PAGES: Omit<DocMeta, "title">[] = [
  { slug: "quickstart", file: "QUICKSTART.md", group: "Get started", summary: "Monitor your first job in a few minutes." , related: ["pings", "linux-agent", "alerts"] },
  { slug: "pings", file: "PINGS.md", group: "Get started", summary: "Ping URLs, start and fail signals, the JSON heartbeat API." , related: ["run-checks", "import", "linux-agent"] },
  { slug: "linux-agent", file: "AGENT.md", group: "Get started", summary: "Discover crontabs and systemd timers; wrap runs with cs-run." , related: ["pings", "run-checks", "kubernetes"] },
  { slug: "kubernetes", file: "KUBERNETES.md", group: "Get started", summary: "Watch CronJobs with the in-cluster agent." , related: ["linux-agent", "alerts"] },
  { slug: "import", file: "IMPORT.md", group: "Get started", summary: "Bring checks over from Healthchecks.io or Cronitor." , related: ["pings", "quickstart"] },
  { slug: "alerts", file: "ALERTS.md", group: "Alerts and checks", summary: "What every alert tells you, on every channel." , related: ["run-checks", "datadog", "how-it-works"] },
  { slug: "run-checks", file: "RUN_CHECKS.md", group: "Alerts and checks", summary: "Fail runs that exit 0 but did the wrong thing." , related: ["pings", "linux-agent", "alerts"] },
  { slug: "maintenance", file: "MAINTENANCE.md", group: "Alerts and checks", summary: "Skip runs and silence alerts during planned work.", related: ["alerts", "run-checks"] },
  { slug: "oncall", file: "ONCALL.md", group: "Alerts and checks", summary: "Rotations, overrides and escalation until someone acknowledges.", related: ["alerts", "maintenance"] },
  { slug: "sso", file: "SSO.md", group: "Security and identity", summary: "SAML and OpenID Connect with verified domains." , related: ["scim", "security"] },
  { slug: "scim", file: "SCIM.md", group: "Security and identity", summary: "Directory sync for people and groups." , related: ["sso", "audit-streaming"] },
  { slug: "audit-streaming", file: "AUDIT_STREAMING.md", group: "Security and identity", summary: "Stream the audit log to your SIEM." , related: ["datadog", "security"] },
  { slug: "security", file: "SECURITY_OVERVIEW.md", group: "Security and identity", summary: "How JobWatch protects your data." , related: ["sso", "audit-streaming", "deploy"] },
  { slug: "datadog", file: "DATADOG.md", group: "Integrations", summary: "Alerts as events, audit logs and job metrics." , related: ["alerts", "audit-streaming"] },
  { slug: "terraform", file: "TERRAFORM.md", group: "Integrations", summary: "Jobs and maintenance windows as code.", related: ["maintenance", "run-checks"] },
  { slug: "how-it-works", file: "HOW_IT_WORKS.md", group: "Self-hosting", summary: "The services and how a ping becomes an alert." , related: ["deploy", "security"] },
  { slug: "deploy", file: "DEPLOY.md", group: "Self-hosting", summary: "Run JobWatch in your own Kubernetes cluster." , related: ["how-it-works", "security"] },
];
const BY_FILE = Object.fromEntries(PAGES.map((p) => [p.file, p.slug]));

function docsDir(): string {
  const candidates = [process.env.DOCS_DIR, path.join(process.cwd(), "../../docs"), path.join(process.cwd(), "docs"), "/docs"];
  const found = candidates.find((d) => d && fs.existsSync(path.join(d, "QUICKSTART.md")));
  if (!found) throw new Error("docs/ not found: set DOCS_DIR when building the web app");
  return found;
}

/** "Run checks (R54)" → "Run checks": round labels are for engineers reading the repo. */
export function cleanTitle(s: string): string {
  return s.replace(/\s*\((?:R\d+)(?:,\s*R\d+)*\)\s*$/, "").trim();
}

const slugify = (s: string) => s.toLowerCase().replace(/<[^>]+>/g, "").replace(/[^a-z0-9\s-]/g, "").trim().replace(/\s+/g, "-");

export function listDocs(): DocMeta[] {
  const dir = docsDir();
  return PAGES.map((p) => {
    const first = fs.readFileSync(path.join(dir, p.file), "utf8").split("\n").find((l) => l.startsWith("# ")) ?? p.slug;
    return { ...p, title: cleanTitle(first.slice(2)) };
  });
}

export type Rendered = DocMeta & { html: string; toc: { id: string; text: string }[] };

export function renderDoc(slug: string): Rendered | null {
  const meta = listDocs().find((d) => d.slug === slug);
  if (!meta) return null;
  const src = fs.readFileSync(path.join(docsDir(), meta.file), "utf8");
  const toc: { id: string; text: string }[] = [];
  const md = new Marked({ gfm: true });
  md.use({
    renderer: {
      heading({ tokens, depth }) {
        const text = this.parser.parseInline(tokens);
        const clean = depth === 1 ? cleanTitle(text) : text;
        if (depth === 1) return "";                       // the page header renders the title
        const id = slugify(clean);
        if (depth === 2) toc.push({ id, text: clean.replace(/<[^>]+>/g, "") });
        return `<h${depth} id="${id}"><a href="#${id}" class="anchor" aria-hidden="true">#</a>${clean}</h${depth}>`;
      },
      link({ href, title, tokens }) {
        const text = this.parser.parseInline(tokens);
        let h = href ?? "";
        const file = h.replace(/^(\.\/|docs\/)/, "").split("#")[0];
        if (BY_FILE[file]) h = `/docs/${BY_FILE[file]}${h.includes("#") ? "#" + h.split("#")[1] : ""}`;
        else if (/^[a-z0-9-]+(#.*)?$/.test(h) && PAGES.some((p) => p.slug === h.split("#")[0])) h = `/docs/${h}`;
        const ext = /^https?:\/\//.test(h);
        return `<a href="${h}"${title ? ` title="${title}"` : ""}${ext ? ' rel="noopener noreferrer"' : ""}>${text}</a>`;
      },
      blockquote({ tokens }) {
        // GitHub-style callouts: > [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING]
        const inner = this.parser.parse(tokens);
        const m = inner.match(/^<p>\[!(NOTE|TIP|IMPORTANT|WARNING)\]\s*(?:<br>)?\s*/);
        if (!m) return `<blockquote>${inner}</blockquote>`;
        const kind = m[1].toLowerCase();
        const label = { note: "Note", tip: "Tip", important: "Important", warning: "Warning" }[kind];
        return `<div class="callout callout-${kind}" role="note"><p class="callout-title">${label}</p><p>${inner.slice(m[0].length)}</div>`;
      },
      code({ text, lang }) {
        const esc = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return `<div class="code-block"><pre${lang ? ` data-lang="${lang}"` : ""}><code>${esc}</code></pre></div>`;
      },
      table(token) {
        // wide tables scroll inside their own box on phones
        const head = token.header.map((c) => `<th>${this.parser.parseInline(c.tokens)}</th>`).join("");
        const rows = token.rows.map((r) => `<tr>${r.map((c) => `<td>${this.parser.parseInline(c.tokens)}</td>`).join("")}</tr>`).join("");
        return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
      },
    },
  });
  const html = md.parse(src) as string;
  return { ...meta, html, toc };
}

/** For the client-side search box: titles, summaries and section headings. */
export function searchIndex(): { slug: string; title: string; text: string }[] {
  return listDocs().map((d) => {
    const r = renderDoc(d.slug)!;
    return { slug: d.slug, title: d.title, text: [d.summary, ...r.toc.map((t) => t.text)].join(" · ") };
  });
}
