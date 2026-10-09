import Link from "next/link";
import { notFound } from "next/navigation";
import { listDocs, renderDoc } from "@/lib/docs";
import { DocEnhancer } from "@/components/docs-enhance";

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  return listDocs().map((d) => ({ slug: d.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const d = listDocs().find((x) => x.slug === slug);
  return { title: d ? `${d.title} · JobWatch docs` : "JobWatch docs", description: d?.summary };
}

export default async function DocPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const doc = renderDoc(slug);
  if (!doc) notFound();
  const all = listDocs();
  const i = all.findIndex((d) => d.slug === slug);
  const prev = all[i - 1], next = all[i + 1];
  return (
    <div className="grid gap-10 xl:grid-cols-[minmax(0,1fr)_200px]">
      <article className="min-w-0">
        <nav aria-label="Breadcrumb" className="text-sm text-mute">
          <ol className="flex flex-wrap items-center gap-1.5">
            <li><Link href="/docs" className="hover:text-ink">Docs</Link></li><li aria-hidden>/</li>
            <li>{doc.group}</li><li aria-hidden>/</li><li aria-current="page" className="text-ink">{doc.title}</li>
          </ol>
        </nav>
        <h1 className="mt-2 font-display text-4xl font-semibold">{doc.title}</h1>
        <p className="mt-3 text-[17px] text-mute">{doc.summary}</p>
        <div className="doc-content mt-8" dangerouslySetInnerHTML={{ __html: doc.html }} />
        <DocEnhancer />
        {!!doc.related?.length && (
          <section className="mt-12 rounded-[10px] border border-line bg-panel p-5" aria-labelledby="further">
            <h2 id="further" className="font-display text-lg font-semibold">Further reading</h2>
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">{doc.related.map((s) => all.find((d) => d.slug === s)).filter(Boolean).map((d) => (
              <li key={d!.slug}><Link href={`/docs/${d!.slug}`} className="block rounded-md px-3 py-2 hover:bg-raised">
                <span className="font-medium text-accent">{d!.title}</span><span className="block text-sm text-mute">{d!.summary}</span></Link></li>))}</ul>
          </section>)}
        <nav className="mt-14 grid gap-3 border-t border-line pt-6 sm:grid-cols-2" aria-label="Previous and next">
          {prev ? <Link href={`/docs/${prev.slug}`} className="card p-3 text-sm hover:border-accent/60"><span className="text-xs text-mute">Previous</span><span className="block font-medium">{prev.title}</span></Link> : <span />}
          {next && <Link href={`/docs/${next.slug}`} className="card p-3 text-right text-sm hover:border-accent/60"><span className="text-xs text-mute">Next</span><span className="block font-medium">{next.title}</span></Link>}
        </nav>
      </article>
      {doc.toc.length > 1 && (
        <aside className="hidden xl:block">
          <div className="sticky top-20 text-sm">
            <div className="mb-2 text-sm font-semibold">On this page</div>
            <ul className="space-y-1.5">{doc.toc.map((t) => <li key={t.id}><a href={`#${t.id}`} className="text-mute hover:text-ink">{t.text}</a></li>)}</ul>
          </div>
        </aside>)}
    </div>
  );
}
