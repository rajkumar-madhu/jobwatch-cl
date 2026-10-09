/* R61 — /llms.txt: a documentation index for AI agents (the convention Datadog's docs also follow).
   Generated at build time from the same manifest as /docs. */
import { GROUPS, listDocs } from "@/lib/docs";

export const dynamic = "force-static";

export function GET() {
  const docs = listDocs();
  const lines = ["# WeCrew JobWatch", "", "> Monitoring for scheduled jobs: cron, systemd timers, Kubernetes CronJobs, backups and pipelines. " +
    "JobWatch expects every run before it is due and alerts on failed, late and missed runs.", ""];
  for (const g of GROUPS) {
    const list = docs.filter((d) => d.group === g);
    if (g === "Reference") { lines.push("## Reference", "", "- [API reference](/docs/api): every endpoint of the JobWatch API, by area", ""); continue; }
    if (!list.length) continue;
    lines.push(`## ${g}`, "", ...list.map((d) => `- [${d.title}](/docs/${d.slug}): ${d.summary}`), "");
  }
  return new Response(lines.join("\n"), { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
