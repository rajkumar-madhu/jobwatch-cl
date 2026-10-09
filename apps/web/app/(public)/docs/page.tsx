import Link from "next/link";
import { GROUPS, listDocs, searchIndex } from "@/lib/docs";
import { DocsSearchHero } from "@/components/docs-search-hero";

export const dynamic = "force-static";

export default function DocsHome() {
  const docs = listDocs();
  return (
    <div>
      <h1 className="font-display text-4xl font-semibold">JobWatch documentation</h1>
      <p className="mt-3 max-w-[640px] text-[17px] text-mute">Monitor every scheduled job: cron, systemd timers, Kubernetes CronJobs, backups and pipelines.</p>
      <div className="mt-6"><DocsSearchHero index={[...searchIndex(), { slug: "api", title: "API reference", text: "Every endpoint of the JobWatch API" }]} /></div>
      <div className="mt-4 flex flex-wrap gap-2 text-sm">
        <span className="py-1 text-mute">Popular:</span>
        {[["quickstart", "Quick start"], ["pings", "Pings and heartbeat API"], ["linux-agent", "Linux agent"], ["api", "API reference"]].map(([s, l]) => (
          <Link key={s} href={s === "api" ? "/docs/api" : `/docs/${s}`} className="rounded-full border border-line bg-panel px-3 py-1 hover:border-accent hover:text-accent">{l}</Link>))}
      </div>
      <div className="mt-10 space-y-10">
        {GROUPS.filter((g) => g !== "Reference").map((g) => (
          <section key={g} aria-labelledby={`g-${g}`}>
            <h2 id={`g-${g}`} className="mb-3 font-display text-lg font-semibold">{g}</h2>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {docs.filter((d) => d.group === g).map((d) => (
                <Link key={d.slug} href={`/docs/${d.slug}`} className="card block p-4 transition hover:border-accent/60">
                  <div className="font-medium">{d.title}</div><p className="mt-1 text-sm text-mute">{d.summary}</p>
                </Link>))}
            </div>
          </section>))}
        <section aria-labelledby="g-ref">
          <h2 id="g-ref" className="mb-3 font-display text-lg font-semibold">Reference</h2>
          <Link href="/docs/api" className="card block max-w-sm p-4 transition hover:border-accent/60">
            <div className="font-medium">API reference</div><p className="mt-1 text-sm text-mute">Every endpoint of the JobWatch API, by area.</p>
          </Link>
        </section>
      </div>
    </div>
  );
}
