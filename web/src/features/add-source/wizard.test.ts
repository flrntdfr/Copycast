import { describe, expect, it } from "vitest";

import {
  autoCandidate,
  findExistingMirror,
  goBack,
  initialWizardState,
  looseSourceKey,
  wizardReducer,
  type WizardState,
} from "./wizard";
import {
  DEFAULT_POLICY,
  policyFromDefaults,
  policySchema,
  toBackfill,
  toMirrorCreate,
} from "./policy-form";
import { mirrorDefaults } from "@/test/factories";
import { candidate, mirror, probeResult } from "@/test/factories";

describe("wizardReducer", () => {
  const url = "https://podcast.example/";

  it("creates without asking when the probe finds exactly one Source that is not a lone video", () => {
    const only = candidate({ item_count: 6 });
    expect(autoCandidate(probeResult([only]))).toEqual(only);
    expect(autoCandidate(probeResult([candidate({ item_count: 1 })]))).toBeNull();
    expect(
      autoCandidate(probeResult([candidate(), candidate({ candidate_token: "b" })])),
    ).toBeNull();
  });

  it("shows the candidate list and ends on created", () => {
    const [a, b] = [
      candidate({ candidate_token: "a" }),
      candidate({ candidate_token: "b", title: "Bonus" }),
    ];
    let state = wizardReducer(initialWizardState(url), {
      type: "probed",
      probe: probeResult([a, b]),
    });
    expect(state.step).toBe("candidates");
    state = wizardReducer(state, { type: "created", mirror: mirror() });
    expect(state.step).toBe("created");
  });

  it("goes back to the probe from anywhere later", () => {
    const [a, b] = [candidate({ candidate_token: "a" }), candidate({ candidate_token: "b" })];
    const candidates: WizardState = { step: "candidates", url, probe: probeResult([a, b]) };
    expect(goBack(candidates, "probe")).toEqual(initialWizardState(url));
    expect(goBack(candidates, "created")).toBe(candidates);
    const created: WizardState = { step: "created", url, mirror: mirror() };
    expect(goBack(created, "candidates")).toEqual(initialWizardState(url));
  });

  it("restarts on a new URL", () => {
    const state = wizardReducer(
      { step: "created", url, mirror: mirror() },
      { type: "restart", url: "https://other.example" },
    );
    expect(state).toEqual(initialWizardState("https://other.example"));
  });
});

describe("findExistingMirror", () => {
  it("matches loosely on host and path", () => {
    const existing = mirror({ source_url: "https://www.Podcast.example/feed.xml/" });
    expect(findExistingMirror([existing], "podcast.example/feed.xml")).toBe(existing);
    expect(findExistingMirror([existing], "https://podcast.example/other.xml")).toBeNull();
    expect(looseSourceKey("HTTPS://WWW.Example.com/a/")).toBe("example.com/a");
  });
});

describe("policy form", () => {
  it("validates the fields per mode", () => {
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "latest", latest_n: "" }).success,
    ).toBe(false);
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "rolling", latest_n: "" }).success,
    ).toBe(false);
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "automatic", retention_days: "" }).success,
    ).toBe(true);
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "automatic", retention_days: 0 }).success,
    ).toBe(false);
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "latest", latest_n: "5" }).success,
    ).toBe(true);
    const bad = policySchema.safeParse({ ...DEFAULT_POLICY, mode: "selection", selection: "1-x" });
    expect(bad.success).toBe(false);
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "selection", selection: "" }).success,
    ).toBe(true);
  });

  it("starts from the operator's default policy, Automatic when none is stored", () => {
    expect(DEFAULT_POLICY.mode).toBe("automatic");
    expect(policyFromDefaults(undefined)).toEqual(DEFAULT_POLICY);
    expect(policyFromDefaults(mirrorDefaults())).toMatchObject({
      mode: "automatic",
      retention_days: 7,
    });
    expect(
      policyFromDefaults(mirrorDefaults({ backfill: { mode: "automatic", retention_days: null } }))
        .retention_days,
    ).toBeUndefined();
    expect(policyFromDefaults(mirrorDefaults({ backfill: { mode: "all" } }))).toMatchObject({
      mode: "all",
      retention_days: 7,
    });
    expect(
      policyFromDefaults(mirrorDefaults({ backfill: { mode: "rolling", latest_n: 4 } })),
    ).toMatchObject({ mode: "rolling", latest_n: 4 });
  });

  it("builds the MirrorCreate body from the candidate and the policy", () => {
    const chosen = candidate({ candidate_token: "tok", source_url: "https://x.example/feed" });
    expect(toBackfill({ mode: "selection", selection: " 1-42, 180 ", follow: false })).toEqual({
      mode: "selection",
      selection: "1-42, 180",
    });
    expect(toBackfill({ mode: "selection", selection: "", follow: false })).toEqual({
      mode: "selection",
    });
    expect(toBackfill({ mode: "rolling", latest_n: 10, follow: true })).toEqual({
      mode: "rolling",
      latest_n: 10,
    });
    expect(toBackfill({ mode: "automatic", retention_days: 30, follow: true })).toEqual({
      mode: "automatic",
      retention_days: 30,
    });
    expect(toBackfill({ mode: "automatic", follow: true })).toEqual({
      mode: "automatic",
      retention_days: null,
    });
    expect(toBackfill({ mode: "latest", latest_n: 5, follow: true })).toEqual({
      mode: "latest",
      latest_n: 5,
    });
    expect(toMirrorCreate(chosen, { mode: "all", follow: true })).toEqual({
      source_url: "https://x.example/feed",
      candidate_token: "tok",
      backfill: { mode: "all" },
      follow: true,
    });
  });
});
