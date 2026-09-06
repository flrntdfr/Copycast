import { describe, expect, it } from "vitest";

import {
  findExistingMirror,
  goBack,
  initialWizardState,
  looseSourceKey,
  wizardReducer,
  type WizardState,
} from "./wizard";
import { DEFAULT_POLICY, policySchema, toBackfill, toMirrorCreate } from "./policy-form";
import { candidate, mirror, probeResult } from "@/test/factories";

describe("wizardReducer", () => {
  const url = "https://podcast.example/";

  it("skips the candidate step when the probe finds exactly one Source", () => {
    const only = candidate();
    const state = wizardReducer(initialWizardState(url), {
      type: "probed",
      probe: probeResult([only]),
    });
    expect(state.step).toBe("policy");
    expect(state.step === "policy" && state.candidate).toEqual(only);
  });

  it("shows the candidate list for two Sources and moves on with the chosen one", () => {
    const [a, b] = [
      candidate({ candidate_token: "a" }),
      candidate({ candidate_token: "b", title: "Bonus" }),
    ];
    let state = wizardReducer(initialWizardState(url), {
      type: "probed",
      probe: probeResult([a, b]),
    });
    expect(state.step).toBe("candidates");
    state = wizardReducer(state, { type: "choose", token: "b" });
    expect(state.step === "policy" && state.candidate.title).toBe("Bonus");
    state = wizardReducer(state, { type: "created", mirror: mirror() });
    expect(state.step).toBe("created");
  });

  it("goes back to the candidate list only when there was one", () => {
    const [a, b] = [candidate({ candidate_token: "a" }), candidate({ candidate_token: "b" })];
    const policy: WizardState = { step: "policy", url, probe: probeResult([a, b]), candidate: b };
    expect(goBack(policy, "candidates").step).toBe("candidates");
    const single: WizardState = { step: "policy", url, probe: probeResult([a]), candidate: a };
    expect(goBack(single, "candidates")).toEqual(initialWizardState(url));
    expect(goBack(policy, "created")).toBe(policy);
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
    expect(findExistingMirror([existing], "https://podcast.example/feed.xml")).toBe(existing);
    expect(findExistingMirror([existing], "https://podcast.example/other.xml")).toBeNull();
    expect(looseSourceKey("podcast.example/feed.xml#x")).toBe("podcast.example/feed.xml");
  });
});

describe("policy form", () => {
  it("requires a count under Latest N and validates Selection expressions", () => {
    expect(
      policySchema.safeParse({ ...DEFAULT_POLICY, mode: "latest", latest_n: "" }).success,
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

  it("builds the MirrorCreate body from the candidate and the policy", () => {
    const chosen = candidate({
      candidate_token: "tok-1",
      source_url: "https://podcast.example/feed.xml",
    });
    expect(toBackfill({ mode: "selection", selection: " 1-42, 180 ", follow: false })).toEqual({
      mode: "selection",
      selection: "1-42, 180",
    });
    expect(toBackfill({ mode: "selection", selection: "", follow: false })).toEqual({
      mode: "selection",
    });
    expect(toBackfill({ mode: "latest", latest_n: 5, follow: true })).toEqual({
      mode: "latest",
      latest_n: 5,
    });
    expect(toMirrorCreate(chosen, { mode: "all", follow: true })).toEqual({
      source_url: "https://podcast.example/feed.xml",
      candidate_token: "tok-1",
      backfill: { mode: "all" },
      follow: true,
    });
  });
});
