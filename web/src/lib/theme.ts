/**
 * Theme preference shared by the inline no-flash script in index.html and the
 * React ThemeToggle. The stored value is `copycast.theme` in localStorage.
 */
import { useSyncExternalStore } from "react";

export const THEME_STORAGE_KEY = "copycast.theme";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const PREFERENCES: readonly ThemePreference[] = ["light", "dark", "system"];

const listeners = new Set<() => void>();

function readStorage(): ThemePreference {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (raw && (PREFERENCES as readonly string[]).includes(raw)) return raw as ThemePreference;
  } catch {
    // Private mode or storage disabled: fall back to system.
  }
  return "system";
}

export function getThemePreference(): ThemePreference {
  if (typeof window === "undefined") return "system";
  return readStorage();
}

export function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

/** Mirrors the inline script: toggles the `dark` class on <html>. */
export function applyTheme(preference: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(preference);
  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("dark", resolved === "dark");
    document.documentElement.style.colorScheme = resolved;
  }
  return resolved;
}

export function setThemePreference(preference: ThemePreference): void {
  try {
    if (preference === "system") window.localStorage.removeItem(THEME_STORAGE_KEY);
    else window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Storage unavailable: the preference lives for this page only.
  }
  applyTheme(preference);
  for (const listener of listeners) listener();
}

export function nextThemePreference(current: ThemePreference): ThemePreference {
  const index = PREFERENCES.indexOf(current);
  return PREFERENCES[(index + 1) % PREFERENCES.length] ?? "system";
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const media =
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  const onMedia = () => {
    if (getThemePreference() === "system") applyTheme("system");
    listener();
  };
  media?.addEventListener("change", onMedia);
  const onStorage = (event: StorageEvent) => {
    if (event.key === THEME_STORAGE_KEY) {
      applyTheme(getThemePreference());
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    media?.removeEventListener("change", onMedia);
    window.removeEventListener("storage", onStorage);
  };
}

export function useTheme(): {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  cycle: () => void;
} {
  const preference = useSyncExternalStore(subscribe, getThemePreference, () => "system" as const);
  const resolved = resolveTheme(preference);
  return {
    preference,
    resolved,
    setPreference: setThemePreference,
    cycle: () => setThemePreference(nextThemePreference(preference)),
  };
}

/**
 * The exact script inlined in index.html before first paint; exported so a
 * test can assert the two stay in sync.
 */
export const INLINE_THEME_SCRIPT = `(function(){try{var k="${THEME_STORAGE_KEY}";var v=localStorage.getItem(k);var d=v==="dark"||(v!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);var h=document.documentElement;h.classList.toggle("dark",d);h.style.colorScheme=d?"dark":"light";}catch(e){}})();`;
