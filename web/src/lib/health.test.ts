import { describe, expect, it } from "vitest";

import { describeHealth, healthTone } from "./health";

const now = new Date("2024-01-15T12:00:00Z");
const base = {
  health: { status: "ok" as const, reason: null },
  paused: false,
  last_refresh_attempt_at: "2024-01-15T09:00:00Z",
  last_refresh_success_at: "2024-01-15T09:00:00Z",
  last_error: null,
};

describe("describeHealth", () => {
  it("shows the last Refresh relative to now when healthy", () => {
    const view = describeHealth(base, now);
    expect(view.tone).toBe("success");
    expect(view.text).toBe("Refreshed 3 hours ago");
  });

  it("says Paused first, whatever the status", () => {
    const view = describeHealth(
      { ...base, paused: true, health: { status: "error", reason: "boom" } },
      now,
    );
    expect(view.text).toBe("Paused");
    expect(view.detail).toContain("boom");
    expect(view.tone).toBe("destructive");
  });

  it("handles a Mirror that was never refreshed", () => {
    const view = describeHealth(
      {
        ...base,
        health: { status: "never", reason: null },
        last_refresh_attempt_at: null,
        last_refresh_success_at: null,
      },
      now,
    );
    expect(view.text).toBe("Never refreshed");
    expect(view.tone).toBe("muted");
  });

  it("reports failures since the last success with the error as detail", () => {
    const view = describeHealth(
      { ...base, health: { status: "error", reason: null }, last_error: "HTTP 503" },
      now,
    );
    expect(view.tone).toBe("destructive");
    expect(view.text).toBe("Failing since 3 hours ago");
    expect(view.detail).toBe("HTTP 503");
  });

  it("reports warnings", () => {
    const view = describeHealth(
      { ...base, health: { status: "warn", reason: "2 items failed" } },
      now,
    );
    expect(view.tone).toBe("warning");
    expect(view.text).toBe("Warnings; refreshed 3 hours ago");
    expect(view.detail).toBe("2 items failed");
  });

  it("falls back to a muted tone for unknown statuses", () => {
    expect(healthTone("mystery")).toBe("muted");
  });
});
