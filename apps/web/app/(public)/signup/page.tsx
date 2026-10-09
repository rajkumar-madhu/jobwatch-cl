"use client";
import Link from "next/link";
import { useState } from "react";
import { API, AuthShell, Divider, ProviderButtons, useNext, useProviders } from "@/components/auth-shell";

/** R51 sign-up. A social provider creates the account on first use; a work email whose company has
 *  verified SSO goes to that provider and joins the company's org; anyone else gets Keycloak's
 *  registration form with the email filled in (email verification is on in the realm). */
export default function Signup() {
  const providers = useProviders();
  const next = useNext("/onboarding");
  const [email, setEmail] = useState("");
  const qs = (o: Record<string, string>) => new URLSearchParams({ next, ...o }).toString();
  return (
    <AuthShell title="Start monitoring free" subtitle="5 jobs free, no card. Upgrade or self-host whenever you like."
      footer={<>Already have an account? <Link href="/login" className="font-medium text-accent hover:underline">Sign in</Link></>}>
      <ProviderButtons providers={providers} href={(a) => `${API}/auth/signup?${qs({ idp: a })}`} />
      {providers.length > 0 && <Divider />}
      <form onSubmit={(e) => { e.preventDefault(); location.href = `${API}/auth/signup?${qs({ email })}`; }} className="space-y-3">
        <label className="block text-sm font-medium">Work email
          <input className="input mt-1.5 py-2.5" type="email" required autoComplete="email" autoFocus value={email}
            onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" /></label>
        <button className="btn btn-primary w-full py-2.5" type="submit">Create account</button>
      </form>
      <ul className="mt-6 space-y-1.5 text-xs text-mute">
        <li>Your first job can be pinging within two minutes of signing up.</li>
        <li>If your company already uses JobWatch with single sign-on, you will join its workspace.</li>
      </ul>
    </AuthShell>
  );
}
