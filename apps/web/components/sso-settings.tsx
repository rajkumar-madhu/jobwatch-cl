"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, mutate } from "@/lib/api";
import { ErrorBox } from "@/components/ui";

type Domain = { id: string; domain: string; verified_at: string | null; txt_name: string; txt_value: string };
type Sso = {
  configured: boolean; protocol?: "oidc" | "saml"; display_name?: string; enabled?: boolean; enforce?: boolean; default_role?: string;
  config?: Record<string, string>; has_client_secret?: boolean; redirect_uri: string; domains: Domain[];
};

const inp = "input mt-1";

/** R43: per-organization single sign-on. Admins configure the IdP and domains; owners turn on enforcement. */
export function SsoSettings({ role }: { role?: string }) {
  const qc = useQueryClient();
  const isAdmin = role === "admin" || role === "owner";
  const q = useQuery({ queryKey: ["sso"], queryFn: () => api<Sso>("/api/v1/sso"), enabled: isAdmin, retry: false });
  const d = q.data;
  const [f, setF] = useState({ protocol: "oidc", display_name: "", issuer: "", client_id: "", client_secret: "", entity_id: "", sso_url: "", signing_cert: "", default_role: "viewer" });
  useEffect(() => {
    if (d?.configured) setF((x) => ({ ...x, protocol: d.protocol!, display_name: d.display_name ?? "", default_role: d.default_role ?? "viewer",
      issuer: d.config?.issuer ?? "", client_id: d.config?.client_id ?? "", entity_id: d.config?.entity_id ?? "", sso_url: d.config?.sso_url ?? "" }));
  }, [d]);
  const [dom, setDom] = useState("");
  const inv = () => qc.invalidateQueries({ queryKey: ["sso"] });
  const save = useMutation({ onSuccess: inv, mutationFn: (enabled: boolean) => mutate("/api/v1/sso", "put", { body: {
    protocol: f.protocol, display_name: f.display_name, enabled, default_role: f.default_role,
    ...(f.protocol === "oidc" ? { oidc: { issuer: f.issuer, client_id: f.client_id, client_secret: f.client_secret || null } }
      : { saml: { entity_id: f.entity_id, sso_url: f.sso_url, signing_cert: f.signing_cert } }) } as any }) });
  const addDom = useMutation({ onSuccess: () => { setDom(""); inv(); }, mutationFn: () => mutate("/api/v1/sso/domains", "post", { body: { domain: dom } }) });
  const verify = useMutation({ onSuccess: inv, mutationFn: (id: string) => mutate("/api/v1/sso/domains/{domain_id}/verify", "post", { path: { domain_id: id } }) });
  const rmDom = useMutation({ onSuccess: inv, mutationFn: (id: string) => mutate("/api/v1/sso/domains/{domain_id}", "delete", { path: { domain_id: id } }) });
  const enforce = useMutation({ onSuccess: inv, mutationFn: (v: boolean) => mutate("/api/v1/sso/enforce", "put", { body: { enforce: v } }) });
  // R44: SCIM tokens. The secret is only in the create response, shown once.
  const scim = useQuery({ queryKey: ["scim-tokens"], queryFn: () => api<{ base_url: string; tokens: { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null }[] }>("/api/v1/sso/scim-tokens"), enabled: isAdmin });
  const [newTok, setNewTok] = useState<string | null>(null);
  const mkTok = useMutation({ mutationFn: () => mutate("/api/v1/sso/scim-tokens", "post", { body: { name: "Directory sync" } }),
    onSuccess: (r: any) => { setNewTok(r?.token ?? null); qc.invalidateQueries({ queryKey: ["scim-tokens"] }); } });
  // R56: directory groups and the JobWatch role each one grants (owners only)
  const groups = useQuery({ queryKey: ["scim-groups"], queryFn: () => api<{ id: string; display_name: string; role: string | null; members: number }[]>("/api/v1/sso/scim-groups"), enabled: isAdmin });
  const mapRole = useMutation({ mutationFn: ({ id, role }: { id: string; role: string | null }) =>
      mutate("/api/v1/sso/scim-groups/{group_id}/role", "put", { path: { group_id: id }, body: { role } as any }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scim-groups"] }) });
  const rmTok = useMutation({ mutationFn: (id: string) => mutate("/api/v1/sso/scim-tokens/{token_id}", "delete", { path: { token_id: id } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scim-tokens"] }) });

  if (!isAdmin) return <p className="text-mute">Only admins and owners can set up single sign-on for this organization.</p>;
  if (q.error) return <ErrorBox error={q.error} />;
  if (!d) return <div className="skeleton h-24" />;
  const verified = d.domains.some((x) => x.verified_at);
  const err = save.error || addDom.error || verify.error || rmDom.error || enforce.error || mkTok.error || rmTok.error || mapRole.error;
  return (
    <div className="space-y-6">
      <p className="text-mute">Let people sign in with your company identity provider. Anyone with an address in a verified domain joins this
        organization on first sign-in with the role you choose.</p>
      {err && <ErrorBox error={err} />}

      <div className="card p-4">
        <div className="flex items-center justify-between"><h3 className="font-medium">1. Identity provider</h3>
          {d.configured && <span className={`text-xs ${d.enabled ? "text-ok" : "text-mute"}`}>{d.enabled ? "Enabled" : "Disabled"}</span>}</div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label>Protocol<select className={inp} value={f.protocol} onChange={(e) => setF({ ...f, protocol: e.target.value })}><option value="oidc">OpenID Connect</option><option value="saml">SAML 2.0</option></select></label>
          <label>Button label<input className={inp} value={f.display_name} placeholder="Okta" onChange={(e) => setF({ ...f, display_name: e.target.value })} /></label>
          {f.protocol === "oidc" ? <>
            <label className="sm:col-span-2">Issuer URL<input className={`${inp} font-mono`} value={f.issuer} placeholder="https://acme.okta.com" onChange={(e) => setF({ ...f, issuer: e.target.value })} /></label>
            <label>Client ID<input className={`${inp} font-mono`} value={f.client_id} onChange={(e) => setF({ ...f, client_id: e.target.value })} /></label>
            <label>Client secret<input className={`${inp} font-mono`} type="password" value={f.client_secret} placeholder={d.has_client_secret ? "Stored. Leave blank to keep it" : ""} onChange={(e) => setF({ ...f, client_secret: e.target.value })} /></label>
          </> : <>
            <label>IdP entity ID<input className={`${inp} font-mono`} value={f.entity_id} onChange={(e) => setF({ ...f, entity_id: e.target.value })} /></label>
            <label>Single sign-on URL<input className={`${inp} font-mono`} value={f.sso_url} placeholder="https://…" onChange={(e) => setF({ ...f, sso_url: e.target.value })} /></label>
            <label className="sm:col-span-2">Signing certificate<textarea className={`${inp} h-24 font-mono text-xs`} value={f.signing_cert} placeholder="-----BEGIN CERTIFICATE-----" onChange={(e) => setF({ ...f, signing_cert: e.target.value })} /></label>
          </>}
          <label>Role for new members<select className={inp} value={f.default_role} onChange={(e) => setF({ ...f, default_role: e.target.value })}>
            {["viewer", "developer", "sre", "devops"].map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
        </div>
        <div className="mt-3 rounded-md border border-line bg-bg px-3 py-2 text-xs"><span className="text-mute">Register this {f.protocol === "oidc" ? "redirect URI" : "ACS URL"} with your identity provider: </span>
          <code className="break-all font-mono">{d.redirect_uri}</code></div>
        <div className="mt-3 flex gap-2"><button className="btn btn-primary" disabled={save.isPending || !f.display_name} onClick={() => save.mutate(true)}>{d.configured ? "Save and enable" : "Connect identity provider"}</button>
          {d.configured && d.enabled && <button className="btn" onClick={() => save.mutate(false)}>Disable</button>}</div>
      </div>

      <div className="card p-4">
        <h3 className="font-medium">2. Verified email domains</h3>
        <p className="mt-1 text-mute">Only addresses in these domains can sign in through your identity provider.</p>
        <div className="mt-3 divide-y divide-line">{d.domains.map((x) => (
          <div key={x.id} className="py-2">
            <div className="flex items-center justify-between gap-3"><span className="font-mono">{x.domain}</span>
              <span className="flex items-center gap-2">{x.verified_at ? <span className="text-xs text-ok">Verified</span>
                : <button className="btn" disabled={verify.isPending} onClick={() => verify.mutate(x.id)}>Check DNS</button>}
                <button className="text-xs text-mute hover:text-bad" onClick={() => rmDom.mutate(x.id)}>Remove</button></span></div>
            {!x.verified_at && <div className="mt-1 text-xs text-mute">Add a TXT record <code className="font-mono text-ink">{x.txt_name}</code> with value <code className="break-all font-mono text-ink">{x.txt_value}</code></div>}
          </div>))}</div>
        <div className="mt-3 flex gap-2"><input className="input" value={dom} placeholder="acme.com" onChange={(e) => setDom(e.target.value)} />
          <button className="btn whitespace-nowrap" disabled={!dom} onClick={() => addDom.mutate()}>Add domain</button></div>
      </div>

      <div className="card p-4">
        <h3 className="font-medium">3. Require single sign-on</h3>
        <p className="mt-1 text-mute">Members other than owners can then use this organization only after signing in through your identity provider.
          Owners keep password sign-in so a broken identity provider cannot lock everyone out. API keys are not affected.</p>
        <label className="mt-3 flex items-center gap-2"><input type="checkbox" checked={!!d.enforce} disabled={role !== "owner" || !d.enabled || !verified || enforce.isPending}
          onChange={(e) => enforce.mutate(e.target.checked)} />Require single sign-on{role !== "owner" && <span className="text-xs text-mute"> (owners only)</span>}</label>
      </div>

      <div className="card p-4">
        <h3 className="font-medium">4. Directory sync (SCIM)</h3>
        <p className="mt-1 text-mute">Let Okta, Entra ID or another directory add people when they join and remove them the moment they leave.
          Deactivated people lose access on their next request. Owners are never changed by directory sync.</p>
        {scim.data && <div className="mt-3 rounded-md border border-line bg-bg px-3 py-2 text-xs"><span className="text-mute">SCIM base URL: </span><code className="break-all font-mono">{scim.data.base_url}</code></div>}
        {newTok && <div className="mt-3 rounded-md border border-accent/50 bg-accent/[.07] px-3 py-2 text-xs" role="status">
          <div className="font-medium">Copy this token into your directory now. It will not be shown again.</div>
          <div className="mt-1 flex items-center gap-2"><code className="min-w-0 flex-1 break-all font-mono">{newTok}</code>
            <button className="btn" onClick={() => navigator.clipboard?.writeText(newTok)}>Copy</button></div></div>}
        <div className="mt-3 divide-y divide-line">{scim.data?.tokens.map((t) => (
          <div key={t.id} className="flex items-center justify-between gap-3 py-2 text-xs">
            <span><span className="text-ink">{t.name}</span> <code className="ml-1 font-mono text-mute">{t.prefix}…</code></span>
            <span className="flex items-center gap-3 text-mute">{t.last_used_at ? `last used ${new Date(t.last_used_at).toLocaleString()}` : "never used"}
              {role === "owner" && <button className="hover:text-bad" onClick={() => { if (confirm("Revoke this token? Directory sync stops until a new one is set.")) rmTok.mutate(t.id); }}>Revoke</button>}</span>
          </div>))}</div>
        <button className="btn mt-3" disabled={role !== "owner" || mkTok.isPending} onClick={() => mkTok.mutate()}>New SCIM token{role !== "owner" && " (owners only)"}</button>
        <div className="mt-5 border-t border-line pt-4">
          <h4 className="text-sm font-medium">Groups and roles</h4>
          <p className="mt-1 text-xs text-mute">Groups pushed by your directory. Give a group a role and everyone in it gets that role; a person in several
            mapped groups gets the highest one. Owners are never changed by directory sync.</p>
          {groups.data?.length ? (
            <div className="mt-2 divide-y divide-line">{groups.data.map((g) => (
              <div key={g.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="min-w-0"><span className="font-medium">{g.display_name}</span> <span className="whitespace-nowrap text-xs text-mute">{g.members} {g.members === 1 ? "person" : "people"}</span></span>
                <div className="w-44 shrink-0">
                  <select className="input py-1 text-xs" aria-label={`Role for ${g.display_name}`} value={g.role ?? ""} disabled={role !== "owner" || mapRole.isPending}
                    onChange={(e) => mapRole.mutate({ id: g.id, role: e.target.value || null })}>
                    <option value="">No role change</option>
                    {["viewer", "developer", "sre", "devops", "admin"].map((r) => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
              </div>))}</div>
          ) : <p className="mt-2 text-xs text-mute">No groups yet. Assign groups to the JobWatch app in your directory and push them.</p>}
          {role !== "owner" && <p className="mt-2 text-xs text-mute">Only owners can change group roles.</p>}
        </div>
      </div>
    </div>
  );
}
