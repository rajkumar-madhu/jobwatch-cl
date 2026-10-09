import { apiReference } from "@/lib/api-reference";

export const dynamic = "force-static";
export const metadata = { title: "API reference · JobWatch docs" };

const METHOD: Record<string, string> = { GET: "text-ok", POST: "text-accent", PUT: "text-warn", PATCH: "text-warn", DELETE: "text-bad" };

export default function ApiReference() {
  const sections = apiReference();
  const count = sections.reduce((n, s) => n + s.endpoints.length, 0);
  return (
    <div>
      <p className="text-sm text-mute">Reference</p>
      <h1 className="mt-1 font-display text-4xl font-semibold">API reference</h1>
      <div className="doc-content mt-4">
        <p>{count} endpoints, generated from the API&apos;s OpenAPI description. Authenticate with an API key in the <code>X-API-Key</code> header
          (Settings → API keys) or a Keycloak bearer token. Lists use cursor pagination (<code>limit</code>, <code>cursor</code> →
          <code> next_cursor</code>); <code>429</code> responses carry <code>Retry-After</code>. Ping and agent endpoints are on the ingest host:
          see <a href="/docs/pings">Pings and heartbeat API</a>. SCIM 2.0 is served at <code>/scim/v2</code>: see <a href="/docs/scim">Directory sync</a>.</p>
      </div>
      <div className="mt-8 space-y-8">
        {sections.map((s) => (
          <section key={s.tag} aria-labelledby={`t-${s.tag}`}>
            <h2 id={`t-${s.tag}`} className="mb-2 font-display text-xl font-semibold">{s.tag}</h2>
            <div className="overflow-x-auto rounded-lg border border-line">
              <table className="w-full text-sm">
                <tbody>{s.endpoints.map((e) => (
                  <tr key={e.method + e.path} className="border-t border-line first:border-0 align-top">
                    <td className={`w-20 px-3 py-2 font-mono text-xs font-semibold ${METHOD[e.method] ?? ""}`}>{e.method}</td>
                    <td className="px-3 py-2 font-mono text-xs"><span className="whitespace-nowrap">{e.path}</span>
                      {e.params.length > 0 && <span className="block text-mute">?{e.params.join(", ")}</span>}</td>
                    <td className="px-3 py-2 text-mute">{e.summary}{e.body && <span className="ml-1 text-xs">(JSON body)</span>}</td>
                  </tr>))}</tbody>
              </table>
            </div>
          </section>))}
      </div>
    </div>
  );
}
