/* R59 — API reference generated from the same OpenAPI document the web client's types come from
   (openapi.json, refreshed whenever the API changes). Build-time only. */
import "server-only";
import fs from "node:fs";
import path from "node:path";

export type Endpoint = { method: string; path: string; summary: string; params: string[]; body: boolean };
export type Section = { tag: string; endpoints: Endpoint[] };

const HIDE = [/^\/metrics$/, /^\/healthz$/, /^\/readyz$/];
const LABEL: Record<string, string> = { jobs: "Jobs", executions: "Runs", agents: "Agents", alerting: "Alerting", incidents: "Incidents",
  sso: "Single sign-on and SCIM tokens", audit: "Audit-log streams", integrations: "Integrations", import: "Import", auth: "Sign-in",
  org: "Organization", status: "Status pages", analytics: "Analytics", copilot: "AI copilot", billing: "Billing", logs: "Logs",
  topology: "Topology", k8s: "Kubernetes", gdpr: "Data export and deletion", platform: "Platform", overview: "Overview" };

export function apiReference(): Section[] {
  const spec = JSON.parse(fs.readFileSync(path.join(process.cwd(), "openapi.json"), "utf8"));
  const groups: Record<string, Endpoint[]> = {};
  for (const [p, ops] of Object.entries<any>(spec.paths)) {
    if (HIDE.some((r) => r.test(p))) continue;
    for (const [method, op] of Object.entries<any>(ops)) {
      if (!["get", "post", "put", "patch", "delete"].includes(method)) continue;
      const tag = (op.tags?.[0] as string) ?? "other";
      const doc = (op.description ?? "").split("\n").map((l: string) => l.trim()).find(Boolean) ?? "";
      (groups[tag] ??= []).push({
        method: method.toUpperCase(), path: p, summary: (op.summary && op.summary !== op.operationId ? op.summary : "") || doc,
        params: (op.parameters ?? []).filter((x: any) => x.in === "query").map((x: any) => x.name), body: !!op.requestBody,
      });
    }
  }
  return Object.entries(groups).map(([tag, endpoints]) => ({ tag: LABEL[tag] ?? tag, endpoints: endpoints.sort((a, b) => a.path.localeCompare(b.path)) }))
    .sort((a, b) => a.tag.localeCompare(b.tag));
}
