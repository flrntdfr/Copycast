import type { ItemRead } from "@/api/types";
import type { CatalogItemState } from "./search";

/** The one state a Catalog row shows (AGENTS.md "Catalog"); `hidden` rows appear only under a facet. */
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

export function canDelete(item: Pick<ItemRead, "state">): boolean {
  return item.state === "archived";
}

/** The number shown in the `#` column: Source numbering wins, else the Ordinal. */
export function displayNumber(item: Pick<ItemRead, "source_number" | "ordinal">): {
  value: number;
  source: boolean;
} {
  if (item.source_number != null) return { value: item.source_number, source: true };
  return { value: item.ordinal, source: false };
}
