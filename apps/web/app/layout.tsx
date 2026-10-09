// R16: IBM Plex bundled from node_modules (SIL OFL 1.1 — redistribution permitted). Only the
// weights the UI uses; Next fingerprints and serves them from /_next/static.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/playfair-display/600.css";
import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "@/components/providers";
export const metadata: Metadata = { title: "WeCrew JobWatch — never miss a scheduled job", description: "Monitor cron jobs, Kubernetes CronJobs, backups, pipelines and scripts from one platform." };
export default function RootLayout({ children }: { children: React.ReactNode }) {
    // Light is the default (R44); a stored "dark" or "gold" choice wins. Applied before first paint.
  const boot = `try{var t=localStorage.getItem("cs_theme");document.documentElement.classList.add(t==="dark"?"dark":t==="gold"?"gold":"light")}catch(e){document.documentElement.classList.add("light")}`;
  return (<html lang="en" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: boot }} /></head><body><Providers>{children}</Providers></body></html>);
}
