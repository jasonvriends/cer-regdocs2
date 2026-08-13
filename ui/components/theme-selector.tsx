"use client";

import { ChangeEvent, useEffect, useState } from "react";
import { Moon, Monitor, Sun } from "lucide-react";

type ThemePreference = "light" | "dark" | "system";

const STORAGE_KEY = "regdocs-atlas-theme";

function isThemePreference(value: string | null): value is ThemePreference {
  return value === "light" || value === "dark" || value === "system";
}

function applyTheme(preference: ThemePreference) {
  const resolved =
    preference === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;

  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.classList.toggle("dark", resolved === "dark");
  document.documentElement.style.colorScheme = resolved;
}

export function ThemeSelector() {
  const [theme, setTheme] = useState<ThemePreference>("system");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = window.localStorage.getItem(STORAGE_KEY);
    } catch {
      // Keep the system default when browser storage is unavailable.
    }
    setTheme(isThemePreference(saved) ? saved : "system");
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;

    applyTheme(theme);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleSystemChange = () => {
      if (theme === "system") applyTheme("system");
    };

    media.addEventListener("change", handleSystemChange);
    return () => media.removeEventListener("change", handleSystemChange);
  }, [mounted, theme]);

  function handleChange(event: ChangeEvent<HTMLSelectElement>) {
    const preference = event.target.value as ThemePreference;
    applyTheme(preference);
    try {
      window.localStorage.setItem(STORAGE_KEY, preference);
    } catch {
      // The theme still applies for this page when browser storage is unavailable.
    }
    setTheme(preference);
  }

  return (
    <label className="relative flex min-w-fit items-center gap-1.5" title="Color theme">
      <span className="sr-only">Color theme</span>
      {theme === "light" ? <Sun aria-hidden="true" className="size-3.5 text-muted-foreground" /> : theme === "dark" ? <Moon aria-hidden="true" className="size-3.5 text-muted-foreground" /> : <Monitor aria-hidden="true" className="size-3.5 text-muted-foreground" />}
      <select
        aria-label="Color theme"
        className="h-8 rounded-lg border border-border bg-background px-2 text-xs text-foreground outline-none transition focus:ring-2 focus:ring-ring"
        onChange={handleChange}
        value={theme}
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}
