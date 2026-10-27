import { z } from "zod";

import type { ArchiveState, ItemSort, SortOrder } from "@/api/types";
import type { CatalogState } from "@/lib/labels";

/** Rows per `list_items` request; the Catalog loads the next page as you scroll near its end. */
export const CATALOG_PAGE_SIZE = 100;

/** Toolbar facets: the six derived states plus tombstones (hidden otherwise). */
export const CATALOG_FACETS = [
  "listed",
  "delisted",
  "available",
  "queued",
  "archiving",
  "failed",
  "deleted",
] as const;
export type CatalogFacet = (typeof CATALOG_FACETS)[number];

export const catalogSearchDefaults = {
  q: "",
  sort: "ordinal" as ItemSort,
  order: "desc" as SortOrder,
} as const;

export const catalogSearchSchema = z.object({
  q: z.string().default(catalogSearchDefaults.q),
  state: z.enum(CATALOG_FACETS).optional(),
  listed: z.boolean().optional(),
  sort: z.enum(["published", "ordinal", "title", "added"]).default(catalogSearchDefaults.sort),
  order: z.enum(["asc", "desc"]).default(catalogSearchDefaults.order),
});

export type CatalogSearch = z.infer<typeof catalogSearchSchema>;

export interface ListItemsQuery {
  state?: ArchiveState[];
  listed?: boolean;
  q?: string;
  sort: ItemSort;
  order: SortOrder;
  limit: number;
  offset: number;
}

/** What every page of one Catalog listing shares: the filters and the sort. */
export type CatalogFilters = Omit<ListItemsQuery, "limit" | "offset">;

/** Facet -> `list_items` parameters. */
export function facetParams(
  facet: CatalogFacet | undefined,
): Pick<CatalogFilters, "state" | "listed"> {
  switch (facet) {
    case "listed":
      return { state: ["archived"], listed: true };
    case "delisted":
      return { state: ["archived"], listed: false };
    case "available":
      return { state: ["available", "deleted"], listed: true };
    case "queued":
      return { state: ["wanted"] };
    case "archiving":
      return { state: ["archiving"] };
    case "failed":
      return { state: ["failed"] };
    case "deleted":
      return { state: ["deleted"] };
    case undefined:
      return {};
  }
}

export function toCatalogFilters(search: CatalogSearch): CatalogFilters {
  const facet = facetParams(search.state);
  const filters: CatalogFilters = { sort: search.sort, order: search.order };
  if (facet.state) filters.state = facet.state;
  const listed = search.listed ?? facet.listed;
  if (listed !== undefined) filters.listed = listed;
  if (search.q.trim()) filters.q = search.q.trim();
  return filters;
}

/** The `list_items` parameters of page `page` (0-based) of a listing. */
export function pageQuery(filters: CatalogFilters, page: number): ListItemsQuery {
  return { ...filters, limit: CATALOG_PAGE_SIZE, offset: page * CATALOG_PAGE_SIZE };
}

export type CatalogItemState = CatalogState | "hidden";
