/** Pure state for the Add Source dialog; the component only dispatches and renders. */
import type { FeedRead, MirrorRead, ProbeCandidate, ProbeResult } from "@/api/types";

export const WIZARD_STEPS = ["probe", "candidates", "created"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

export type WizardState =
  | { step: "probe"; url: string }
  | { step: "candidates"; url: string; probe: ProbeResult }
  | { step: "created"; url: string; mirror: MirrorRead };

export type WizardAction =
  | { type: "probed"; probe: ProbeResult }
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
    case "probed":
      return { step: "candidates", url: state.url, probe: action.probe };
    case "created":
      return { step: "created", url: state.url, mirror: action.mirror };
    case "goto":
      return goBack(state, action.step);
  }
}

/** Back: land on `target` when the state still holds what that step needs. */
export function goBack(state: WizardState, target: WizardStep): WizardState {
  if (WIZARD_STEPS.indexOf(target) >= WIZARD_STEPS.indexOf(state.step)) return state;
  if (state.step === "created") return initialWizardState(state.url);
  if (target === "probe") return initialWizardState(state.url);
  return state;
}

/**
 * The one candidate to create without asking: a single Source that is not a lone video
 * (a lone video is better sent to an Inbox, so the person is asked).
 */
export function autoCandidate(probe: ProbeResult): ProbeCandidate | null {
  const [only] = probe.candidates;
  if (probe.candidates.length !== 1 || !only) return null;
  return only.item_count === 1 ? null : only;
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
/** Creating lists the Source synchronously; the plan gives it five minutes. */
export const CREATE_TIMEOUT_MS = 5 * 60_000;
