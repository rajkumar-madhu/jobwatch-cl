"use client";
/* Signed-out visitors go to /login instead of seeing the dashboard shell with empty panels. Signed in
   means a stored API key or a live session cookie (GET /auth/session). The cookie is httpOnly and
   set by the API, so this is checked from the browser rather than in Next middleware. /settings stays
   open: it is where /login sends people who use an API key instead of an account. */
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { API, getKey } from "@/lib/api";

const OPEN = ["/settings"];

export function AuthGate({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (ok) return;
    if (getKey() || OPEN.some((p) => path === p || path.startsWith(`${p}/`))) { setOk(true); return; }
    let live = true;
    fetch(`${API}/auth/session`, { credentials: "include" }).then((r) => {
      if (!live) return;
      if (r.status === 401) location.replace(`/login?${new URLSearchParams({ next: path + location.search })}`);
      else setOk(true);   // any other answer: let the pages show their own error states
    }).catch(() => live && setOk(true));
    return () => { live = false; };
  }, [ok, path]);
  if (!ok) return <div className="grid min-h-screen place-items-center text-sm text-mute" aria-busy="true">Loading…</div>;
  return <>{children}</>;
}
