import { z } from "zod";

import type { ItemSort, SortOrder } from "@/api/types";
import type { CatalogState } from "@/lib/labels";

/** Rows per `list_items` request; the Catalog loads the next page as you scroll near its end. */
export const CATALOG_PAGE_SIZE = 100;

export const catalogSearchDefaults = {
  q: "",
  sort: "ordinal" as ItemSort,
  order: "desc" as SortOrder,
} as const;

export const catalogSearchSchema = z.object({
  q: z.string().default(catalogSearchDefaults.q),
  sort: z.enum(["published", "ordinal", "title", "added"]).default(catalogSearchDefaults.sort),
  order: z.enum(["asc", "desc"]).default(catalogSearchDefaults.order),
});

export type CatalogSearch = z.infer<typeof catalogSearchSchema>;

export interface ListItemsQuery {
  q?: string;
  sort: ItemSort;
  order: SortOrder;
  limit: number;
  offset: number;
}

/** What every page of one Catalog listing shares: the filters and the sort. */
export type CatalogFilters = Omit<ListItemsQuery, "limit" | "offset">;

export function toCatalogFilters(search: CatalogSearch): CatalogFilters {
  const filters: CatalogFilters = { sort: search.sort, order: search.order };
  if (search.q.trim()) filters.q = search.q.trim();
  return filters;
}

/** The `list_items` parameters of page `page` (0-based) of a listing. */
export function pageQuery(filters: CatalogFilters, page: number): ListItemsQuery {
  return { ...filters, limit: CATALOG_PAGE_SIZE, offset: page * CATALOG_PAGE_SIZE };
}

export type CatalogItemState = CatalogState | "hidden";
