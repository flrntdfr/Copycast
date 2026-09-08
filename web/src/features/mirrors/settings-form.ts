/**
 * The Mirror Settings form: zod schema, defaults from a MirrorRead, and the
 * `MirrorUpdate` body carrying only what changed (the API applies `model_fields_set`).
 */
import { z } from "zod";

import type { BackfillPolicy, BackfillRequest, MirrorRead, MirrorUpdate } from "@/api/types";
import {
  DEFAULT_RETENTION_DAYS,
  backfillOf,
  modeFields,
  optionalCount,
  refineMode,
} from "@/features/add-source/policy-form";
import { minutesToSeconds, secondsToMinutes } from "@/lib/format";

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
    title: z.string().trim().max(512, "At most 512 characters"),
    follow: z.boolean(),
    ...modeFields,
    engine_options: z.string(),
    language: z
      .string()
      .trim()
      .max(16)
      .regex(/^([a-zA-Z]{2,3}([-_][a-zA-Z0-9]{2,8})*)?$/, "A tag such as fr or pt-BR"),
    min_duration_minutes: optionalCount,
    refresh_interval_hours: optionalCount,
    sync_deletions: z.boolean(),
  })
  .superRefine((value, ctx) => {
    refineMode(value, ctx);
    const options = parseEngineOptions(value.engine_options);
    if (options.error)
      ctx.addIssue({ code: "custom", path: ["engine_options"], message: options.error });
  });

export type MirrorSettingsInput = z.input<typeof mirrorSettingsSchema>;
export type MirrorSettingsValues = z.output<typeof mirrorSettingsSchema>;

export function settingsDefaults(mirror: MirrorRead): MirrorSettingsInput {
  const { backfill } = mirror;
  return {
    source_url: mirror.source_url,
    title: mirror.title_override ?? "",
    follow: mirror.follow,
    mode: backfill.mode,
    latest_n: backfill.latest_n ?? 10,
    // Under Automatic an empty field means "keep forever"; elsewhere the field is unused.
    retention_days:
      backfill.mode === "automatic"
        ? (backfill.retention_days ?? undefined)
        : DEFAULT_RETENTION_DAYS,
    selection: "",
    engine_options: formatEngineOptions(mirror.engine_options),
    language: mirror.preferred_language ?? "",
    min_duration_minutes: mirror.min_duration_seconds
      ? secondsToMinutes(mirror.min_duration_seconds)
      : undefined,
    refresh_interval_hours: mirror.refresh_interval_hours ?? undefined,
    sync_deletions: mirror.sync_deletions,
  };
}

function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The Mirror's current policy in `BackfillRequest` shape, to tell a real change apart. */
export function currentBackfill(policy: BackfillPolicy): BackfillRequest {
  switch (policy.mode) {
    case "latest":
    case "rolling":
      return { mode: policy.mode, latest_n: policy.latest_n ?? 1 };
    case "automatic":
      return { mode: "automatic", retention_days: policy.retention_days ?? null };
    default:
      return { mode: policy.mode };
  }
}

/**
 * Only the fields that differ from the Mirror go into the PATCH body. A Selection
 * with an expression is always sent (it archives those numbers now).
 */
export function toMirrorUpdate(mirror: MirrorRead, values: MirrorSettingsValues): MirrorUpdate {
  const update: MirrorUpdate = {};
  if (values.source_url.trim() !== mirror.source_url) update.source_url = values.source_url.trim();
  // An empty title means "the Source's", sent as null to clear the override.
  const title = values.title.trim() || null;
  if (title !== (mirror.title_override ?? null)) update.title = title;
  if (values.follow !== mirror.follow) update.follow = values.follow;

  const backfill = backfillOf(values);
  if (backfill.selection || !sameJson(backfill, currentBackfill(mirror.backfill)))
    update.backfill = backfill;

  const options = parseEngineOptions(values.engine_options);
  if (options.value && !sameJson(options.value, mirror.engine_options ?? {}))
    update.engine_options = options.value;

  // Overrides: an empty field means "use the global default", sent as null to clear it.
  const language = values.language.trim() || null;
  if (language !== (mirror.preferred_language ?? null)) update.preferred_language = language;
  const seconds = minutesToSeconds(values.min_duration_minutes ?? null);
  if (seconds !== (mirror.min_duration_seconds ?? null)) update.min_duration_seconds = seconds;
  const hours = values.refresh_interval_hours ?? null;
  if (hours !== (mirror.refresh_interval_hours ?? null)) update.refresh_interval_hours = hours;
  if (values.sync_deletions !== mirror.sync_deletions)
    update.sync_deletions = values.sync_deletions;
  return update;
}

export function isEmptyUpdate(update: MirrorUpdate): boolean {
  return Object.keys(update).length === 0;
}

/** What the Retention line under the form says for the mode being edited. */
export function retentionSummary(mode: BackfillPolicy["mode"], retentionDays?: number): string {
  switch (mode) {
    case "rolling":
      return "Archived Episodes outside the newest N are deleted at each Refresh.";
    case "automatic":
      return retentionDays
        ? `Episodes expire ${retentionDays} ${retentionDays === 1 ? "day" : "days"} after their last download.`
        : "Never. Downloaded Episodes stay until you delete them.";
    default:
      return "None. Copycast never deletes from a Mirror on its own.";
  }
}
