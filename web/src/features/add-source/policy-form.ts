import { z } from "zod";

import type { BackfillRequest, MirrorCreate, ProbeCandidate } from "@/api/types";
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

export const policySchema = z
  .object({
    mode: z.enum(["all", "latest", "selection"]),
    latest_n: optionalCount,
    selection: z.string().max(4096).optional(),
    follow: z.boolean(),
  })
  .superRefine((value, ctx) => {
    if (value.mode === "latest" && !value.latest_n) {
      ctx.addIssue({
        code: "custom",
        path: ["latest_n"],
        message: "How many of the latest items?",
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
  });

export type PolicyFormValues = z.output<typeof policySchema>;
export type PolicyFormInput = z.input<typeof policySchema>;

export const DEFAULT_POLICY: PolicyFormValues = {
  mode: "all",
  latest_n: 10,
  selection: "",
  follow: true,
};

export function toBackfill(values: PolicyFormValues): BackfillRequest {
  switch (values.mode) {
    case "all":
      return { mode: "all" };
    case "latest":
      return { mode: "latest", latest_n: values.latest_n ?? 1 };
    case "selection": {
      const expression = values.selection?.trim();
      return expression ? { mode: "selection", selection: expression } : { mode: "selection" };
    }
  }
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
