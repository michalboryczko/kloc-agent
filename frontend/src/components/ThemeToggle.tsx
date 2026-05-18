"use client";

import { useSyncExternalStore } from "react";
import {
  applyTheme,
  persistTheme,
  readStoredTheme,
  resolveTheme,
  type Theme,
} from "@/lib/theme";

const THEME_EVENT = "kloc-theme-change";

function subscribe(cb: () => void) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(THEME_EVENT, cb);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onSystem = () => {
    if (readStoredTheme() === null) {
      applyTheme(media.matches ? "dark" : "light");
      cb();
    }
  };
  media.addEventListener("change", onSystem);
  return () => {
    window.removeEventListener(THEME_EVENT, cb);
    media.removeEventListener("change", onSystem);
  };
}

function getSnapshot(): Theme {
  return resolveTheme();
}

function getServerSnapshot(): Theme {
  return "light";
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    persistTheme(next);
    window.dispatchEvent(new Event(THEME_EVENT));
  }

  const isDark = theme === "dark";
  const label = isDark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      data-test="theme-toggle"
      onClick={toggle}
      aria-label={label}
      title={label}
      className="p-1.5 rounded hover:bg-[var(--color-canvas-sunk)] text-[var(--color-ink-muted)]"
    >
      {isDark ? (
        <svg
          width={15}
          height={15}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      ) : (
        <svg
          width={15}
          height={15}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z" />
        </svg>
      )}
    </button>
  );
}
