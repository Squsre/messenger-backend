"use client";

import { useEffect } from "react";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import {
  ACCENT_ATTR,
  ACCENT_STORAGE_KEY,
} from "@/lib/constants";

export const ACCENT_THEMES = [
  { id: "default", label: "Default", swatch: "oklch(0.205 0 0)", swatchDark: "oklch(0.922 0 0)" },
  { id: "ocean", label: "Ocean", swatch: "oklch(0.52 0.11 205)", swatchDark: "oklch(0.76 0.1 205)" },
  { id: "forest", label: "Forest", swatch: "oklch(0.46 0.11 155)", swatchDark: "oklch(0.75 0.12 155)" },
  { id: "sunset", label: "Sunset", swatch: "oklch(0.58 0.15 50)", swatchDark: "oklch(0.75 0.13 55)" },
  { id: "violet", label: "Violet", swatch: "oklch(0.5 0.16 300)", swatchDark: "oklch(0.7 0.14 300)" },
  { id: "rose", label: "Rose", swatch: "oklch(0.55 0.17 18)", swatchDark: "oklch(0.72 0.14 18)" },
] as const;

export type AccentId = (typeof ACCENT_THEMES)[number]["id"];

function isValidAccent(value: string): value is AccentId {
  return ACCENT_THEMES.some((theme) => theme.id === value);
}

/** Apply the saved accent palette to <html> (client-side only). */
export function applyAccent(accent: string): void {
  if (typeof document === "undefined") return;
  if (accent === "default" || !isValidAccent(accent)) {
    delete document.documentElement.dataset[camelAccentAttr()];
    return;
  }
  document.documentElement.setAttribute(ACCENT_ATTR, accent);
}

function camelAccentAttr(): string {
  // `data-accent` -> `accent` for the dataset API.
  return ACCENT_ATTR.replace(/^data-/, "").replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
}

export function readSavedAccent(): AccentId {
  if (typeof window === "undefined") return "default";
  const saved = window.localStorage.getItem(ACCENT_STORAGE_KEY) ?? "default";
  return isValidAccent(saved) ? saved : "default";
}

/** Restores the accent before paint-adjacent effects run; renders nothing. */
export function AccentTheme() {
  useEffect(() => {
    applyAccent(readSavedAccent());
  }, []);
  return null;
}

export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      <AccentTheme />
      {children}
      <Toaster position="top-center" richColors closeButton />
    </ThemeProvider>
  );
}
