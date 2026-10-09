import { SiteFooter, SiteNav } from "@/components/marketing";
import { DocsNav } from "@/components/docs-nav";
import { DocsTabs } from "@/components/docs-tabs";
import { HostSelector } from "@/components/docs-enhance";
import { GROUPS, listDocs, searchIndex } from "@/lib/docs";

export const metadata = { title: "Docs · WeCrew JobWatch" };

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  const items = [...listDocs().map(({ slug, title, group }) => ({ slug, title, group })), { slug: "api", title: "API reference", group: "Reference" }];
  const index = [...searchIndex(), { slug: "api", title: "API reference", text: "Every endpoint of the JobWatch API, by area" }];
  return (
    <div className="min-h-screen bg-bg text-ink">
      <SiteNav />
      <div className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-3 px-6 py-2">
          <DocsTabs />
          <HostSelector />
        </div>
      </div>
      <div className="mx-auto grid max-w-[1280px] gap-8 px-6 py-10 lg:grid-cols-[230px_minmax(0,1fr)]">
        <aside className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:self-start lg:overflow-y-auto">
          <DocsNav items={items} groups={GROUPS} index={index} />
        </aside>
        <div className="min-w-0">{children}</div>
      </div>
      <SiteFooter />
    </div>
  );
}
