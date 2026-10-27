import { keepPreviousData, useQueries, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { $api } from "@/api/client";
import type { ItemPage, ItemRead } from "@/api/types";
import { catalogStateOf } from "./catalog-state";
import { CATALOG_PAGE_SIZE, type CatalogFilters, pageQuery } from "./search";

/** The next page starts loading once the last row on screen is this close to the end. */
export const LOAD_AHEAD_ROWS = 30;

export interface CatalogPages {
  /** Every loaded row in listing order, each once; tombstones only when asked for. */
  rows: ItemRead[];
  /** The page each row came from. */
  pageOf: number[];
  /** Items fetched so far, tombstones included: what `total` counts. */
  loaded: number;
  total: number;
  /** Nothing to show yet. */
  isPending: boolean;
  /** The rows are the previous listing's while a new filter or sort loads. */
  isPlaceholder: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  /** A page did not load; `retry` asks again. */
  failed: boolean;
  retry: () => void;
  /** Report the rows on screen (indices into `rows`); loads the next page near the end. */
  reach: (firstRow: number | null, lastRow: number | null) => void;
}

/** What the list needs from each page's query; kept small so `combine` stays stable. */
interface PageResult {
  data: ItemPage | undefined;
  dataUpdatedAt: number;
  isError: boolean;
  isFetching: boolean;
  refetch: () => unknown;
}

function pageResults(results: PageResult[]): PageResult[] {
  return results.map(({ data, dataUpdatedAt, isError, isFetching, refetch }) => ({
    data,
    dataUpdatedAt,
    isError,
    isFetching,
    refetch,
  }));
}

interface View {
  key: string;
  /** Pages requested so far. */
  count: number;
  /** Pages on screen; only these refetch when the Catalog is invalidated. */
  first: number;
  last: number;
}

/**
 * The Catalog as a list that grows as you scroll: page n is `list_items` at offset
 * n × CATALOG_PAGE_SIZE, each its own query under the `list_items` key, so SSE events and
 * mutations (`useInvalidateCatalog`) refetch only the pages on screen. Pages scrolled away
 * keep their rows and refetch, if stale, when they come back. A new filter or sort starts
 * over from the first page, which shows the previous listing until it arrives.
 */
export function useCatalogPages(
  feedId: string,
  filters: CatalogFilters,
  { showHidden = false }: { showHidden?: boolean } = {},
): CatalogPages {
  const queryClient = useQueryClient();
  const key = JSON.stringify([feedId, filters]);
  const [view, setView] = useState<View>({ key, count: 1, first: 0, last: 0 });
  let current = view;
  if (view.key !== key) {
    current = { key, count: 1, first: 0, last: 0 };
    setView(current);
  }

  const init = (page: number) => ({
    params: { path: { feed_id: feedId }, query: pageQuery(filters, page) },
  });
  // A page off screen sits out invalidations, unless it has nothing to show yet.
  const enabled = (page: number) =>
    (page >= current.first && page <= current.last) ||
    queryClient.getQueryData(
      $api.queryOptions("get", "/api/feeds/{feed_id}/items", init(page)).queryKey,
    ) === undefined;

  const head = $api.useQuery("get", "/api/feeds/{feed_id}/items", init(0), {
    placeholderData: keepPreviousData,
    enabled: enabled(0),
  });
  const tail = useQueries({
    queries: Array.from({ length: current.count - 1 }, (_, index) => ({
      ...$api.queryOptions("get", "/api/feeds/{feed_id}/items", init(index + 1)),
      enabled: enabled(index + 1),
    })),
    combine: pageResults,
  });

  const { rows, pageOf, loaded } = useMemo(() => {
    const rows: ItemRead[] = [];
    const pageOf: number[] = [];
    const seen = new Set<string>();
    for (const [page, data] of [head.data, ...tail.map((result) => result.data)].entries()) {
      if (!data) break;
      for (const item of data.items) {
        // A refetched page can repeat a neighbour's item until that page refetches too.
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        if (!showHidden && catalogStateOf(item) === "hidden") continue;
        rows.push(item);
        pageOf.push(page);
      }
    }
    return { rows, pageOf, loaded: seen.size };
  }, [head.data, tail, showHidden]);

  const pages: PageResult[] = [head, ...tail];
  const last = pages[pages.length - 1] ?? head;
  // The freshest page knows the total best.
  const total = pages.reduce<{ at: number; total: number }>(
    (best, page) =>
      page.data && page.dataUpdatedAt >= best.at
        ? { at: page.dataUpdatedAt, total: page.data.total }
        : best,
    { at: -1, total: 0 },
  ).total;
  const isPlaceholder = head.isPlaceholderData;
  const hasMore =
    !isPlaceholder &&
    !!last.data &&
    last.data.items.length >= CATALOG_PAGE_SIZE &&
    current.count * CATALOG_PAGE_SIZE < total;
  const isLoadingMore = current.count > 1 && !last.data && last.isFetching;
  const failed = pages.find((page) => !page.data && page.isError);
  const count = current.count;
  const newestLoaded = !!last.data;

  const reach = useCallback(
    (firstRow: number | null, lastRow: number | null) => {
      const first = (firstRow != null ? pageOf[firstRow] : undefined) ?? 0;
      const onScreen = (lastRow != null ? pageOf[lastRow] : undefined) ?? count - 1;
      const grow = hasMore && (lastRow == null || lastRow >= pageOf.length - LOAD_AHEAD_ROWS);
      setView((v) => {
        if (v.key !== key) return v;
        const grown = grow && v.count === count;
        const next = grown ? count + 1 : v.count;
        // The newest page stays live until it has rows to place: dropping out for the
        // render in which its data arrives would refetch it as soon as it is back.
        const last = grown ? count : newestLoaded ? onScreen : count - 1;
        if (v.first === first && v.last === last && v.count === next) return v;
        return { ...v, first, last, count: next };
      });
    },
    [key, pageOf, count, hasMore, newestLoaded],
  );

  return {
    rows,
    pageOf,
    loaded,
    total,
    isPending: head.isPending,
    isPlaceholder,
    isLoadingMore,
    hasMore,
    failed: !!failed,
    retry: () => void failed?.refetch(),
    reach,
  };
}
