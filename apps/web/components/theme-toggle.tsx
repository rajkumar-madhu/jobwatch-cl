"use client";
import { Gem, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

/** Three themes: light (default), dark (Finspot tokens) and gold (Obsidian Gold, the CSM Silks palette).
 *  The choice is stored per browser and applied before paint by app/layout.tsx. */
type Theme = "dark" | "light" | "gold";
const ORDER: Theme[] = ["light", "dark", "gold"];
const NEXT_LABEL: Record<Theme, string> = { light: "Dark theme", dark: "Obsidian Gold theme", gold: "Light theme" };

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const c = document.documentElement.classList;
    setTheme(c.contains("light") ? "light" : c.contains("gold") ? "gold" : "dark");
  }, []);
  const flip = () => {
    const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
    const c = document.documentElement.classList;
    c.remove("light", "dark", "gold");
    c.add(next);
    try { localStorage.setItem("cs_theme", next); } catch {}
    setTheme(next);
  };
  const Icon = theme === "light" ? Moon : theme === "dark" ? Gem : Sun;
  return (
    <button type="button" onClick={flip} className={`inline-flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-mute hover:bg-raised hover:text-ink ${className}`}
      aria-label={`Switch to ${NEXT_LABEL[theme]}`}>
      <Icon size={14} aria-hidden />{NEXT_LABEL[theme]}
    </button>
  );
}
