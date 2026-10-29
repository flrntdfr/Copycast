import { describe, expect, it } from "vitest";

import { bucket, mirror } from "@/test/factories";
import { syncView } from "./sync";

describe("syncView", () => {
  it("is green for a healthy Mirror with a successful Refresh", () => {
    expect(syncView(mirror({ last_refresh_success_at: "2024-01-15T09:00:00Z" }))).toEqual({
      tone: "success",
      at: "2024-01-15T09:00:00Z",
    });
  });

  it("is orange for a Mirror that is failing, paused or never refreshed", () => {
    const failing = mirror({ health: { status: "error", reason: "HTTP 503" } });
    expect(syncView(failing)).toEqual({ tone: "warning", at: failing.last_refresh_success_at });
    expect(syncView(mirror({ paused: true })).tone).toBe("warning");
    expect(
      syncView(
        mirror({
          health: { status: "never", reason: null },
          last_refresh_attempt_at: null,
          last_refresh_success_at: null,
        }),
      ),
    ).toEqual({ tone: "warning", at: null });
  });

  it("dates a Bucket Feed from its last Request and is green once it holds anything", () => {
    expect(syncView(bucket({ request_count: 2 }), "2024-01-15T09:00:00Z")).toEqual({
      tone: "success",
      at: "2024-01-15T09:00:00Z",
    });
    // Uploads make Episodes without a Request: still synced, with no time to show yet.
    expect(syncView(bucket({ episode_count: 1 }))).toEqual({ tone: "success", at: null });
    expect(syncView(bucket())).toEqual({ tone: "warning", at: null });
  });
});
