import { describe, expect, it } from "vitest";

import { describeCounts, refreshCounts, refreshDuration } from "./RefreshesTable";
import {
  currentBackfill,
  formatEngineOptions,
  mirrorSettingsSchema,
  parseEngineOptions,
  retentionSummary,
  settingsDefaults,
  toMirrorUpdate,
} from "./settings-form";
import { job, mirror } from "@/test/factories";

describe("settings form", () => {
  const base = mirror({
    backfill: { mode: "latest", latest_n: 5 },
    engine_options: { format: "bestaudio" },
  });

  it("parses and formats engine options", () => {
    expect(parseEngineOptions("")).toEqual({ value: {}, error: null });
    expect(parseEngineOptions('{"a": 1}')).toEqual({ value: { a: 1 }, error: null });
    expect(parseEngineOptions("[1]").error).toMatch(/JSON object/);
    expect(parseEngineOptions("{").error).toBeTruthy();
    expect(formatEngineOptions({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(formatEngineOptions({})).toBe("");
  });

  it("validates the schema", () => {
    const values = settingsDefaults(base);
    expect(mirrorSettingsSchema.safeParse(values).success).toBe(true);
    expect(mirrorSettingsSchema.safeParse({ ...values, engine_options: "nope" }).success).toBe(
      false,
    );
    expect(
      mirrorSettingsSchema.safeParse({ ...values, mode: "selection", selection: "x" }).success,
    ).toBe(false);
    expect(mirrorSettingsSchema.safeParse({ ...values, source_url: " " }).success).toBe(false);
  });

  it("sends only what changed", () => {
    const parsed = mirrorSettingsSchema.parse(settingsDefaults(base));
    expect(toMirrorUpdate(base, parsed)).toEqual({});
    expect(toMirrorUpdate(base, { ...parsed, follow: false })).toEqual({ follow: false });
    expect(toMirrorUpdate(base, { ...parsed, mode: "all" })).toEqual({ backfill: { mode: "all" } });
    expect(toMirrorUpdate(base, { ...parsed, latest_n: 7 })).toEqual({
      backfill: { mode: "latest", latest_n: 7 },
    });
    expect(
      toMirrorUpdate(base, { ...parsed, source_url: "https://moved.example/feed.xml " }),
    ).toEqual({
      source_url: "https://moved.example/feed.xml",
    });
    expect(toMirrorUpdate(base, { ...parsed, engine_options: '{"format":"bestaudio"}' })).toEqual(
      {},
    );
    expect(toMirrorUpdate(base, { ...parsed, engine_options: "" })).toEqual({ engine_options: {} });
    // A title of your own is kept over the Source's; clearing it restores the Source's.
    expect(toMirrorUpdate(base, { ...parsed, title: " Mine " })).toEqual({ title: "Mine" });
    const titled = { ...base, title: "Mine", title_override: "Mine" };
    expect(settingsDefaults(titled).title).toBe("Mine");
    expect(toMirrorUpdate(titled, { ...parsed, title: "" })).toEqual({ title: null });
    // Overrides: a value sets it, clearing it sends null so the default applies again.
    expect(toMirrorUpdate(base, { ...parsed, language: " fr " })).toEqual({
      preferred_language: "fr",
    });
    expect(toMirrorUpdate(base, { ...parsed, min_duration_minutes: 5 })).toEqual({
      min_duration_seconds: 300,
    });
    const overridden = { ...base, preferred_language: "fr", min_duration_seconds: 300 };
    expect(
      toMirrorUpdate(overridden, mirrorSettingsSchema.parse(settingsDefaults(overridden))),
    ).toEqual({});
    expect(
      toMirrorUpdate(overridden, { ...parsed, language: "", min_duration_minutes: undefined }),
    ).toEqual({ preferred_language: null, min_duration_seconds: null });
  });

  it("tells a real mode change apart for Rolling and Automatic", () => {
    const rolling = mirror({ backfill: { mode: "rolling", latest_n: 10, retention_days: null } });
    const parsed = mirrorSettingsSchema.parse(settingsDefaults(rolling));
    expect(toMirrorUpdate(rolling, parsed)).toEqual({});
    expect(toMirrorUpdate(rolling, { ...parsed, latest_n: 5 })).toEqual({
      backfill: { mode: "rolling", latest_n: 5 },
    });
    expect(toMirrorUpdate(rolling, { ...parsed, mode: "automatic", retention_days: 7 })).toEqual({
      backfill: { mode: "automatic", retention_days: 7 },
    });

    const forever = mirror({
      backfill: { mode: "automatic", latest_n: null, retention_days: null },
    });
    const defaults = settingsDefaults(forever);
    expect(defaults.retention_days).toBeUndefined();
    expect(toMirrorUpdate(forever, mirrorSettingsSchema.parse(defaults))).toEqual({});
    expect(
      toMirrorUpdate(forever, { ...mirrorSettingsSchema.parse(defaults), retention_days: 3 }),
    ).toEqual({ backfill: { mode: "automatic", retention_days: 3 } });
    expect(currentBackfill({ mode: "latest", latest_n: 4 })).toEqual({
      mode: "latest",
      latest_n: 4,
    });
    expect(currentBackfill({ mode: "all" })).toEqual({ mode: "all" });

    expect(retentionSummary("all")).toMatch(/never deletes/);
    expect(retentionSummary("rolling")).toMatch(/outside the newest N/);
    expect(retentionSummary("automatic", 1)).toBe(
      "Episodes expire 1 day after their last download.",
    );
    expect(retentionSummary("automatic")).toMatch(/^Never/);
  });

  it("always sends a Selection with numbers so they get archived", () => {
    const selectionMirror = mirror({ backfill: { mode: "selection", latest_n: null } });
    const parsed = mirrorSettingsSchema.parse({
      ...settingsDefaults(selectionMirror),
      selection: "1-3",
    });
    expect(toMirrorUpdate(selectionMirror, parsed)).toEqual({
      backfill: { mode: "selection", selection: "1-3" },
    });
    const empty = mirrorSettingsSchema.parse(settingsDefaults(selectionMirror));
    expect(toMirrorUpdate(selectionMirror, empty)).toEqual({});
  });
});

describe("refresh rows", () => {
  it("reads the counts the refresh job reports", () => {
    const counts = refreshCounts(
      job({ result: { listed: 120, new: 3, delisted: 1, wanted: 3, enqueued: 3 } }),
    );
    expect(describeCounts(counts)).toBe("120 listed · 3 new · 1 delisted · 3 queued");
    expect(refreshCounts(job({ result: null }))).toBeNull();
    expect(refreshCounts(job({ result: { status: "ok" } }))).toBeNull();
  });

  it("computes the duration from the timestamps", () => {
    expect(
      refreshDuration(
        job({
          status: "succeeded",
          started_at: "2024-01-15T09:00:00Z",
          finished_at: "2024-01-15T09:01:05Z",
        }),
      ),
    ).toBe("1:05");
    expect(refreshDuration(job({ status: "queued" }))).toBe("");
  });
});
