import { z } from "zod";

import type { BackfillRequest, MirrorCreate, MirrorDefaults, ProbeCandidate } from "@/api/types";
import { parseSelection, selectionIsValid } from "@/lib/selection";

/** Number inputs report NaN or "" when empty; treat those as "not given". */
export const optionalCount = z.preprocess(
  (value) =>
    value === "" || value === null || (typeof value === "number" && Number.isNaN(value))
      ? undefined
      : value,
  z.coerce
    .number()
    .int("Whole numbers only")
    .min(1, "At least 1")
    .max(100_000, "At most 100,000")
    .optional(),
);

/** Days an Automatic Mirror keeps an Episode after its last download; empty keeps forever. */
export const optionalDays = z.preprocess(
  (value) =>
    value === "" || value === null || (typeof value === "number" && Number.isNaN(value))
      ? undefined
      : value,
  z.coerce
    .number()
    .int("Whole days only")
    .min(1, "At least 1 day")
    .max(3650, "At most 3,650 days")
    .optional(),
);

export const MODES = ["all", "rolling", "automatic", "selection", "latest"] as const;
export const DEFAULT_RETENTION_DAYS = 7;

/** The fields shared by the wizard and the Settings form; each schema extends them. */
export const modeFields = {
  mode: z.enum(MODES),
  latest_n: optionalCount,
  retention_days: optionalDays,
  selection: z.string().max(4096).optional(),
};

export function refineMode(
  value: { mode: (typeof MODES)[number]; latest_n?: number; selection?: string },
  ctx: z.RefinementCtx,
): void {
  if ((value.mode === "latest" || value.mode === "rolling") && !value.latest_n) {
    ctx.addIssue({
      code: "custom",
      path: ["latest_n"],
      message: value.mode === "rolling" ? "How many to keep?" : "How many of the latest items?",
    });
  }
  if (value.mode === "selection" && value.selection?.trim()) {
    const parsed = parseSelection(value.selection);
    if (!selectionIsValid(parsed)) {
      ctx.addIssue({
        code: "custom",
        path: ["selection"],
        message: `Cannot read ${parsed.invalid.map((t) => `"${t}"`).join(", ")}`,
      });
    }
  }
}

export const policySchema = z
  .object({
    ...modeFields,
    follow: z.boolean(),
  })
  .superRefine(refineMode);

export type PolicyFormValues = z.output<typeof policySchema>;
export type PolicyFormInput = z.input<typeof policySchema>;

export const DEFAULT_POLICY: PolicyFormValues = {
  mode: "automatic",
  latest_n: 10,
  retention_days: DEFAULT_RETENTION_DAYS,
  selection: "",
  follow: true,
};

/** The wizard's starting values: the operator's default policy from Settings, else the built-in. */
export function policyFromDefaults(defaults: MirrorDefaults | undefined): PolicyFormValues {
  const backfill = defaults?.backfill;
  if (!backfill || backfill.mode === "selection" || backfill.mode === "latest")
    return DEFAULT_POLICY;
  return {
    ...DEFAULT_POLICY,
    mode: backfill.mode,
    latest_n: backfill.latest_n ?? DEFAULT_POLICY.latest_n,
    retention_days:
      backfill.mode === "automatic"
        ? (backfill.retention_days ?? undefined)
        : DEFAULT_RETENTION_DAYS,
  };
}

/** The `BackfillRequest` for the mode and its own fields (the others are dropped). */
export function backfillOf(values: {
  mode: (typeof MODES)[number];
  latest_n?: number;
  retention_days?: number;
  selection?: string;
}): BackfillRequest {
  switch (values.mode) {
    case "all":
      return { mode: "all" };
    case "latest":
      return { mode: "latest", latest_n: values.latest_n ?? 1 };
    case "rolling":
      return { mode: "rolling", latest_n: values.latest_n ?? 1 };
    case "automatic":
      return { mode: "automatic", retention_days: values.retention_days ?? null };
    case "selection": {
      const expression = values.selection?.trim();
      return expression ? { mode: "selection", selection: expression } : { mode: "selection" };
    }
  }
}

export function toBackfill(values: PolicyFormValues): BackfillRequest {
  return backfillOf(values);
}

export function toMirrorCreate(
  candidate: ProbeCandidate,
  values: PolicyFormValues,
  engineOptions?: Record<string, unknown>,
): MirrorCreate {
  return {
    source_url: candidate.source_url,
    candidate_token: candidate.candidate_token,
    backfill: toBackfill(values),
    follow: values.follow,
    ...(engineOptions && Object.keys(engineOptions).length
      ? { engine_options: engineOptions }
      : {}),
  };
}
