import { describe, expect, it } from "vitest";

import {
  clearUserStorage,
  getStorageUser,
  setStorageUser,
  storageKey,
  userPrefix,
} from "./storage";
import { THEME_STORAGE_KEY } from "./theme";

describe("per-user storage keys", () => {
  it("uses the pre-3.0 key until a User is known, then that User's prefix", () => {
    expect(storageKey("player")).toBe("copycast.player");
    expect(setStorageUser("alice")).toBe(true);
    expect(setStorageUser("alice")).toBe(false);
    expect(getStorageUser()).toBe("alice");
    expect(storageKey("player")).toBe("copycast.u.alice.player");
    expect(storageKey("listening.v1")).toBe("copycast.u.alice.listening.v1");
    expect(userPrefix("bob")).toBe("copycast.u.bob.");
  });

  it("lets the first User adopt the pre-3.0 keys once, then deletes them", () => {
    window.localStorage.setItem("copycast.player", '{"rate":1.5}');
    window.localStorage.setItem("copycast.listening.v1", '{"i1":{}}');
    window.localStorage.setItem("copycast.buckets.open", "1");
    window.localStorage.setItem("copycast.u.alice.buckets.open", "0");
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");

    setStorageUser("alice");
    expect(window.localStorage.getItem("copycast.u.alice.player")).toBe('{"rate":1.5}');
    expect(window.localStorage.getItem("copycast.u.alice.listening.v1")).toBe('{"i1":{}}');
    // Her own value wins over the old one.
    expect(window.localStorage.getItem("copycast.u.alice.buckets.open")).toBe("0");
    expect(window.localStorage.getItem("copycast.player")).toBeNull();
    expect(window.localStorage.getItem("copycast.buckets.open")).toBeNull();
    // The theme stays global.
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    setStorageUser("bob");
    expect(window.localStorage.getItem("copycast.u.bob.player")).toBeNull();
  });

  it("clears one User's keys and nobody else's", () => {
    window.localStorage.setItem("copycast.u.alice.player", "{}");
    window.localStorage.setItem("copycast.u.alice.player.track", "{}");
    window.localStorage.setItem("copycast.u.alicia.player", "{}");
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");

    clearUserStorage("alice");
    expect(window.localStorage.getItem("copycast.u.alice.player")).toBeNull();
    expect(window.localStorage.getItem("copycast.u.alice.player.track")).toBeNull();
    expect(window.localStorage.getItem("copycast.u.alicia.player")).toBe("{}");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});
