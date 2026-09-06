/**
 * The Mirror Settings form: zod schema, defaults from a MirrorRead, and the
 * `MirrorUpdate` body carrying only what changed (the API applies `model_fields_set`).
 */
import { z } from "zod";

import type { BackfillRequest, MirrorRead, MirrorUpdate } from "@/api/types";
import { optionalCount } from "@/features/add-source/policy-form";
import { parseSelection, selectionIsValid } from "@/lib/selection";

export type EngineOptions = Record<string, unknown>;

/** Parse the JSON textarea; an empty text means "no feed-level options". */
export function parseEngineOptions(
  text: string,
): { value: EngineOptions; error: null } | { value: null; error: string } {
  const trimmed = text.trim();
  if (!trimmed) return { value: {}, error: null };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (error) {
    return { value: null, error: error instanceof Error ? error.message : "Invalid JSON" };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {
      value: null,
      error: 'Engine options must be a JSON object, e.g. {"format": "bestaudio"}',
    };
  }
  return { value: parsed as EngineOptions, error: null };
}

export function formatEngineOptions(options: EngineOptions | null | undefined): string {
  if (!options || Object.keys(options).length === 0) return "";
  return JSON.stringify(options, null, 2);
}

export const mirrorSettingsSchema = z
  .object({
    source_url: z.string().trim().min(1, "A Source URL is required").max(2048),
    follow: z.boolean(),
    mode: z.enum(["all", "latest", "selection"]),
    latest_n: optionalCount,
    selection: z.string().max(4096).optional(),
    engine_options: z.string(),
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
    const options = parseEngineOptions(value.engine_options);
    if (options.error)
      ctx.addIssue({ code: "custom", path: ["engine_options"], message: options.error });
  });

export type MirrorSettingsInput = z.input<typeof mirrorSettingsSchema>;
export type MirrorSettingsValues = z.output<typeof mirrorSettingsSchema>;

export function settingsDefaults(mirror: MirrorRead): MirrorSettingsInput {
  return {
    source_url: mirror.source_url,
    follow: mirror.follow,
    mode: mirror.backfill.mode,
    latest_n: mirror.backfill.latest_n ?? 10,
    selection: "",
    engine_options: formatEngineOptions(mirror.engine_options),
  };
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function backfillOf(values: MirrorSettingsValues): BackfillRequest {
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

/**
 * Only the fields that differ from the Mirror go into the PATCH body. A Selection
 * with an expression is always sent (it archives those numbers now).
 */
export function toMirrorUpdate(mirror: MirrorRead, values: MirrorSettingsValues): MirrorUpdate {
  const update: MirrorUpdate = {};
  if (values.source_url.trim() !== mirror.source_url) update.source_url = values.source_url.trim();
  if (values.follow !== mirror.follow) update.follow = values.follow;

  const backfill = backfillOf(values);
  const currentBackfill: BackfillRequest =
    mirror.backfill.mode === "latest"
      ? { mode: "latest", latest_n: mirror.backfill.latest_n ?? 1 }
      : { mode: mirror.backfill.mode };
  if (backfill.selection || !sameJson(backfill, currentBackfill)) update.backfill = backfill;

  const options = parseEngineOptions(values.engine_options);
  if (options.value && !sameJson(options.value, mirror.engine_options ?? {}))
    update.engine_options = options.value;
  return update;
}

export function isEmptyUpdate(update: MirrorUpdate): boolean {
  return Object.keys(update).length === 0;
}
