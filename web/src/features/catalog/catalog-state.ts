import type { ItemRead } from "@/api/types";
import type { CatalogItemState } from "./search";

/** The one state a Catalog row shows (AGENTS.md "Catalog"); `hidden` rows (deleted and no longer
 * listed) are never shown: `useCatalogPages` drops them. */
export function catalogStateOf(item: Pick<ItemRead, "state" | "listed">): CatalogItemState {
  switch (item.state) {
    case "archived":
      return item.listed ? "listed" : "delisted";
    case "available":
    case "deleted":
      return item.listed ? "available" : "hidden";
    case "wanted":
      return "queued";
    case "archiving":
      return "archiving";
    case "failed":
      return "failed";
  }
}

export function isTombstone(item: Pick<ItemRead, "state">): boolean {
  return item.state === "deleted";
}

export function canArchive(item: Pick<ItemRead, "state">): boolean {
  return item.state === "available" || item.state === "deleted" || item.state === "failed";
}

/** An archived Episode that is not locked: a locked one is never deleted, by hand either. */
export function canDelete(item: Pick<ItemRead, "state" | "archive_flag">): boolean {
  return item.state === "archived" && !item.archive_flag;
}

/** The number shown in the `#` column: Source numbering wins, else the Ordinal. */
export function displayNumber(item: Pick<ItemRead, "source_number" | "ordinal">): {
  value: number;
  source: boolean;
} {
  if (item.source_number != null) return { value: item.source_number, source: true };
  return { value: item.ordinal, source: false };
}

/**
 * Listed but shorter than the Mirror's minimum length (its own or the Default): the policy
 * ignores it. An archived, queued or failed item is never "ignored", whatever its length.
 */
export function isBelowMinimum(
  item: Pick<ItemRead, "duration_seconds" | "state">,
  minDurationSeconds: number | null | undefined,
): boolean {
  if (!minDurationSeconds || item.duration_seconds == null) return false;
  if (item.state !== "available" && item.state !== "deleted") return false;
  return item.duration_seconds < minDurationSeconds;
}
