"use client";
import Link from "next/link";
import { useState } from "react";
import { API, AuthShell, Divider, ProviderButtons, useNext, useProviders } from "@/components/auth-shell";

/** R51 sign-in. Routes: a configured social provider, the company's SSO (found from the email domain
 *  via /auth/discover), or Keycloak's password page with the email filled in. */
export default function Login() {
  const providers = useProviders();
  const next = useNext("/");
  const [email, setEmail] = useState("");
  const [sso, setSso] = useState<string | null>(null);   // provider name when the domain has SSO
  const [busy, setBusy] = useState(false);
  const qs = (o: Record<string, string>) => new URLSearchParams({ next, ...o }).toString();

  const onEmail = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true);
    try {
      const r = await fetch(`${API}/auth/discover?${new URLSearchParams({ email })}`);
      const d = r.ok ? await r.json() : { sso: false };
      if (d.sso) { setSso(d.provider ?? "your company"); location.href = `${API}/auth/sso?${qs({ email })}`; return; }
    } catch { /* fall through to the password page */ }
    location.href = `${API}/auth/login?${qs({ login_hint: email })}`;
  };

  return (
    <AuthShell title="Sign in to JobWatch" subtitle="Welcome back."
      footer={<>New to JobWatch? <Link href="/signup" className="font-medium text-accent hover:underline">Create an account</Link>
        <p className="mt-3 text-xs">Using an API key instead? <Link href="/settings" className="underline">Paste it in Settings</Link>.</p></>}>
      <ProviderButtons providers={providers} href={(a) => `${API}/auth/login?${qs({ idp: a })}`} />
      {providers.length > 0 && <Divider />}
      <form onSubmit={onEmail} className="space-y-3">
        <label className="block text-sm font-medium">Work email
          <input className="input mt-1.5 py-2.5" type="email" required autoComplete="email" autoFocus value={email}
            onChange={(e) => { setEmail(e.target.value); setSso(null); }} placeholder="you@company.com" /></label>
        <button className="btn btn-primary w-full py-2.5" type="submit" disabled={busy}>{sso ? `Continuing with ${sso}…` : busy ? "Checking…" : "Continue"}</button>
      </form>
      <p className="mt-3 text-xs text-mute">If your company uses single sign-on, you will go straight to it. Otherwise you will enter your password on the next step, where you can also reset it.</p>
    </AuthShell>
  );
}
