import { z } from "zod";

import type { ArchiveState, ItemSort, SortOrder } from "@/api/types";
import type { CatalogState } from "@/lib/labels";

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
  page: 1,
  sort: "published" as ItemSort,
  order: "desc" as SortOrder,
} as const;

export const catalogSearchSchema = z.object({
  q: z.string().default(catalogSearchDefaults.q),
  state: z.enum(CATALOG_FACETS).optional(),
  listed: z.boolean().optional(),
  page: z.coerce.number().int().min(1).default(catalogSearchDefaults.page),
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

/** Facet -> `list_items` parameters. */
export function facetParams(
  facet: CatalogFacet | undefined,
): Pick<ListItemsQuery, "state" | "listed"> {
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

export function toListItemsQuery(search: CatalogSearch): ListItemsQuery {
  const facet = facetParams(search.state);
  const query: ListItemsQuery = {
    sort: search.sort,
    order: search.order,
    limit: CATALOG_PAGE_SIZE,
    offset: (search.page - 1) * CATALOG_PAGE_SIZE,
  };
  if (facet.state) query.state = facet.state;
  const listed = search.listed ?? facet.listed;
  if (listed !== undefined) query.listed = listed;
  if (search.q.trim()) query.q = search.q.trim();
  return query;
}

export type CatalogItemState = CatalogState | "hidden";
