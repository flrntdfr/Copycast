import { describe, expect, it } from "vitest";

import html from "../../index.html?raw";

import {
  INLINE_THEME_SCRIPT,
  THEME_STORAGE_KEY,
  applyTheme,
  getThemePreference,
  nextThemePreference,
  setThemePreference,
} from "./theme";

describe("theme", () => {
  it("persists the preference under copycast.theme and toggles the dark class", () => {
    setThemePreference("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(getThemePreference()).toBe("dark");

    setThemePreference("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    setThemePreference("system");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(getThemePreference()).toBe("system");
  });

  it("cycles light -> dark -> system", () => {
    expect(nextThemePreference("light")).toBe("dark");
    expect(nextThemePreference("dark")).toBe("system");
    expect(nextThemePreference("system")).toBe("light");
  });

  it("resolves system to the media query", () => {
    expect(applyTheme("system")).toBe("light");
  });

  it("keeps the inline no-flash script in index.html in sync", () => {
    expect(html).toContain(INLINE_THEME_SCRIPT);
  });
});
