import { Sidebar } from "@/components/sidebar";
import { CommandPalette } from "@/components/command-palette";
import { AuthGate } from "@/components/auth-gate";
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (<AuthGate><div className="flex min-h-screen flex-col lg:flex-row"><Sidebar /><main className="min-w-0 flex-1">{children}</main></div><CommandPalette /></AuthGate>);
}
