/** Pure wizard state for Add Source; the component only dispatches and renders. */
import type { FeedRead, MirrorRead, ProbeCandidate, ProbeResult } from "@/api/types";

export const WIZARD_STEPS = ["probe", "candidates", "policy", "created"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

export type WizardState =
  | { step: "probe"; url: string }
  | { step: "candidates"; url: string; probe: ProbeResult }
  | { step: "policy"; url: string; probe: ProbeResult; candidate: ProbeCandidate }
  | { step: "created"; url: string; mirror: MirrorRead };

export type WizardAction =
  | { type: "probed"; probe: ProbeResult }
  | { type: "choose"; token: string }
  | { type: "created"; mirror: MirrorRead }
  | { type: "restart"; url: string }
  | { type: "goto"; step: WizardStep };

export function initialWizardState(url: string): WizardState {
  return { step: "probe", url };
}

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "restart":
      return initialWizardState(action.url);
    case "probed": {
      const { probe } = action;
      const [only] = probe.candidates;
      if (probe.candidates.length === 1 && only) {
        return { step: "policy", url: state.url, probe, candidate: only };
      }
      return { step: "candidates", url: state.url, probe };
    }
    case "choose": {
      if (state.step !== "candidates" && state.step !== "policy") return state;
      const candidate = state.probe.candidates.find((c) => c.candidate_token === action.token);
      if (!candidate) return state;
      return { step: "policy", url: state.url, probe: state.probe, candidate };
    }
    case "created":
      return { step: "created", url: state.url, mirror: action.mirror };
    case "goto":
      return goBack(state, action.step);
  }
}

/** Browser back: land on `target` when the state still holds what that step needs. */
export function goBack(state: WizardState, target: WizardStep): WizardState {
  if (WIZARD_STEPS.indexOf(target) >= WIZARD_STEPS.indexOf(state.step)) return state;
  if (state.step === "created") return initialWizardState(state.url);
  if (target === "probe") return initialWizardState(state.url);
  if (target === "candidates" && (state.step === "policy" || state.step === "candidates")) {
    if (state.probe.candidates.length <= 1) return initialWizardState(state.url);
    return { step: "candidates", url: state.url, probe: state.probe };
  }
  return state;
}

/** Comparison-only normalization (host case, `www.`, fragment, trailing slashes); the server 409 is the authority. */
export function looseSourceKey(url: string): string {
  const trimmed = url.trim();
  try {
    const parsed = new URL(trimmed.includes("://") ? trimmed : `https://${trimmed}`);
    const host = parsed.host.toLowerCase().replace(/^www\./, "");
    const path = parsed.pathname.replace(/\/+$/, "");
    return `${host}${path}${parsed.search}`;
  } catch {
    return trimmed.toLowerCase();
  }
}

export function findExistingMirror(
  feeds: readonly FeedRead[],
  sourceUrl: string,
): MirrorRead | null {
  const key = looseSourceKey(sourceUrl);
  for (const feed of feeds) {
    if (feed.kind === "mirror" && looseSourceKey(feed.source_url) === key) return feed;
  }
  return null;
}

/** "Everything (N)" needs the count; null when the probe does not know it. */
export function candidateItemCount(candidate: ProbeCandidate | null | undefined): number | null {
  return candidate?.item_count ?? null;
}

export const PROBE_TIMEOUT_MS = 5 * 60_000;
/** Selection mode lists synchronously; the plan gives it five minutes. */
export const CREATE_SELECTION_TIMEOUT_MS = 5 * 60_000;
