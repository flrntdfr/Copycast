import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, formatEta, formatRelative } from "./format";

describe("format", () => {
  it("formats bytes with SI units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(4407)).toBe("4.4 kB");
    expect(formatBytes(1_234_567_890)).toBe("1.2 GB");
    expect(formatBytes(null)).toBe("");
  });

  it("formats durations", () => {
    expect(formatDuration(59)).toBe("0:59");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatDuration(-1)).toBe("");
  });

  it("formats ETAs", () => {
    expect(formatEta(30)).toBe("30 s");
    expect(formatEta(180)).toBe("3 min");
    expect(formatEta(4320)).toBe("1 h 12 min");
  });

  it("formats relative times", () => {
    const now = new Date("2024-01-15T12:00:00Z");
    expect(formatRelative("2024-01-15T11:59:30Z", now)).toBe("just now");
    expect(formatRelative("2024-01-15T09:00:00Z", now)).toBe("3 hours ago");
    expect(formatRelative(null, now)).toBe("");
  });
});
