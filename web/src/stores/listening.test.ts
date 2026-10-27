import { describe, expect, it, vi } from "vitest";

import {
  LISTENING_MAX_ENTRIES,
  LISTENING_STORAGE_KEY,
  readListening,
  resumeAt,
  writeListening,
  type ListeningEntry,
} from "./listening";

const NOW = 1_800_000_000_000;

function entry(patch: Partial<ListeningEntry> = {}): ListeningEntry {
  return { t: 600, d: 3600, at: NOW, finished: false, ...patch };
}

describe("resumeAt", () => {
  it("starts over without an entry, when Finished, barely started or nearly over", () => {
    expect(resumeAt(null, NOW)).toBe(0);
    expect(resumeAt(entry({ finished: true }), NOW)).toBe(0);
    expect(resumeAt(entry({ t: 9 }), NOW)).toBe(0);
    expect(resumeAt(entry({ t: 3575 }), NOW)).toBe(0);
    expect(resumeAt(entry({ t: 3560 }), NOW)).toBe(3560);
    expect(resumeAt(entry({ t: 3590, d: null }), NOW)).toBe(3590);
  });

  it("rewinds more the longer you were away, never below 0", () => {
    expect(resumeAt(entry({ at: NOW - 30_000 }), NOW)).toBe(600);
    expect(resumeAt(entry({ at: NOW - 2 * 60_000 }), NOW)).toBe(597);
    expect(resumeAt(entry({ at: NOW - 2 * 3_600_000 }), NOW)).toBe(590);
    expect(resumeAt(entry({ at: NOW - 2 * 86_400_000 }), NOW)).toBe(580);
    expect(resumeAt(entry({ t: 12, at: NOW - 2 * 86_400_000 }), NOW)).toBe(0);
  });
});

describe("listening storage", () => {
  it("keeps the most recently saved Episodes up to the cap", () => {
    for (let index = 0; index <= LISTENING_MAX_ENTRIES; index += 1)
      writeListening(`item-${index}`, entry({ t: index, at: NOW + index }));
    expect(readListening("item-0")).toBeNull();
    expect(readListening("item-1")).toMatchObject({ t: 1 });
    expect(readListening(`item-${LISTENING_MAX_ENTRIES}`)).toMatchObject({
      t: LISTENING_MAX_ENTRIES,
    });
    const stored = JSON.parse(window.localStorage.getItem(LISTENING_STORAGE_KEY) ?? "{}") as object;
    expect(Object.keys(stored)).toHaveLength(LISTENING_MAX_ENTRIES);
  });

  it("tolerates corrupt JSON and drops invalid entries", () => {
    window.localStorage.setItem(LISTENING_STORAGE_KEY, "{oops");
    expect(readListening("a")).toBeNull();
    writeListening("a", entry());
    expect(readListening("a")).toEqual(entry());

    window.localStorage.setItem(
      LISTENING_STORAGE_KEY,
      JSON.stringify({ a: { t: "x" }, b: entry({ d: null }), c: entry({ d: -1 }) }),
    );
    expect(readListening("a")).toBeNull();
    expect(readListening("b")).toEqual(entry({ d: null }));
    expect(readListening("c")).toBeNull();
    window.localStorage.setItem(LISTENING_STORAGE_KEY, "[1, 2]");
    expect(readListening("0")).toBeNull();
  });

  it("survives a storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    expect(() => writeListening("a", entry())).not.toThrow();
    expect(readListening("a")).toBeNull();
  });
});
