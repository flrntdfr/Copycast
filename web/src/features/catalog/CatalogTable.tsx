import {
  type Cell,
  type ColumnFiltersState,
  type ExpandedState,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  type Row,
  type RowSelectionState,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";
import { useWindowVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ListMusic, Loader2 } from "lucide-react";
import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { $api } from "@/api/client";
import type { FeedRead, ItemRead, ItemSort } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CatalogToolbar } from "./CatalogToolbar";
import { RowDetails } from "./RowDetails";
import { SelectionBar } from "./SelectionBar";
import { buildColumns } from "./columns";
import { isBelowMinimum } from "./catalog-state";
import { useBulkActions } from "./mutations";
import { type CatalogSearch, toCatalogFilters } from "./search";
import { useCatalogPages } from "./useCatalogPages";
import { count } from "@/lib/labels";
import { cn } from "@/lib/utils";

const COLUMNS_STORAGE_KEY = "copycast.catalog.columns";

/** Which header sorts by which `list_items` sort key. */
const SORTABLE: Partial<Record<string, ItemSort>> = {
  number: "ordinal",
  title: "title",
  published: "published",
};

/** Columns that give way under `md`. */
const HIDE_SMALL = new Set(["published", "duration", "size", "downloads"]);
const MD_QUERY = "(min-width: 48rem)";

function subscribeMd(listener: () => void): () => void {
  const media = window.matchMedia(MD_QUERY);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}

/**
 * Whether the `md` columns show. A full-width cell must span exactly the columns on screen:
 * under the fixed layout, a wider `colSpan` adds empty columns that take the title's room.
 */
function useMdUp(): boolean {
  return useSyncExternalStore(
    subscribeMd,
    () => window.matchMedia(MD_QUERY).matches,
    () => true,
  );
}

/** First guesses for the virtualizer; every rendered row is measured. */
const ROW_ESTIMATE = 49;
const DETAILS_ESTIMATE = 180;
/** Rows rendered beyond each edge of the viewport, so Tab always finds the next one. */
const OVERSCAN = 10;

function readColumnVisibility(): VisibilityState {
  try {
    const raw = window.localStorage.getItem(COLUMNS_STORAGE_KEY);
    if (raw) return JSON.parse(raw) as VisibilityState;
  } catch {
    // ignore
  }
  return {};
}

/** One line of the virtual list: a Catalog row, or the details under an expanded one. */
interface Entry {
  key: string;
  row: Row<ItemRead>;
  details: boolean;
}

interface CatalogRowProps {
  row: Row<ItemRead>;
  /** Visible cells; a new array when columns or their visibility change. */
  cells: Cell<ItemRead, unknown>[];
  index: number;
  selected: boolean;
  expanded: boolean;
  ignored: boolean;
  measure: (element: HTMLTableRowElement | null) => void;
}

/** Memoized so scrolling renders only the rows that come into view. */
const CatalogRow = memo(function CatalogRow({
  row,
  cells,
  index,
  selected,
  expanded,
  ignored,
  measure,
}: CatalogRowProps) {
  return (
    <TableRow
      ref={measure}
      data-index={index}
      aria-rowindex={index + 2}
      data-state={selected ? "selected" : undefined}
      data-expanded={expanded || undefined}
      data-testid="catalog-row"
      data-item-id={row.original.id}
      data-ignored={ignored || undefined}
      className={cn("cursor-pointer", ignored && "opacity-60")}
      onClick={() => row.toggleExpanded()}
    >
      {cells.map((cell) => (
        <TableCell
          key={cell.id}
          className={cn(
            HIDE_SMALL.has(cell.column.id) && "hidden md:table-cell",
            cell.column.id === "title" && "max-w-[14rem] md:max-w-md",
          )}
        >
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </TableCell>
      ))}
    </TableRow>
  );
});

export interface CatalogTableProps {
  feed: FeedRead;
  search: CatalogSearch;
  onSearchChange: (patch: Partial<CatalogSearch>) => void;
}

export function CatalogTable({ feed, search, onSearchChange }: CatalogTableProps) {
  const feedKind = feed.kind === "mirror" ? "mirror" : "bucket";
  const filters = useMemo(() => toCatalogFilters(search), [search]);
  const pages = useCatalogPages(feed.id, filters);
  const { rows, reach } = pages;
  const bulk = useBulkActions(feed.id, feedKind);

  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(readColumnVisibility);
  const [columnFilters] = useState<ColumnFiltersState>([]);
  const lastToggled = useRef<number | null>(null);
  const rowsRef = useRef(rows);
  useLayoutEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

  useEffect(() => {
    try {
      window.localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(columnVisibility));
    } catch {
      // ignore
    }
  }, [columnVisibility]);

  // Stable, so the columns (and every rendered row) survive a page loading or refetching.
  const onToggleRow = useCallback((row: Row<ItemRead>, checked: boolean, shiftKey: boolean) => {
    const index = row.index;
    setRowSelection((current) => {
      const next = { ...current };
      const from =
        shiftKey && lastToggled.current != null ? Math.min(lastToggled.current, index) : index;
      const to =
        shiftKey && lastToggled.current != null ? Math.max(lastToggled.current, index) : index;
      for (let i = from; i <= to; i += 1) {
        const id = rowsRef.current[i]?.id;
        if (!id) continue;
        if (checked) next[id] = true;
        else delete next[id];
      }
      return next;
    });
    lastToggled.current = index;
  }, []);

  // The Mirror's effective minimum length greys out the items the policy ignores.
  const defaults = $api.useQuery("get", "/api/settings/defaults", undefined, {
    enabled: feed.kind === "mirror",
    staleTime: 60_000,
  });
  const minimum = useMemo(() => {
    if (feed.kind !== "mirror") return null;
    if (feed.min_duration_seconds)
      return { seconds: feed.min_duration_seconds, source: "mirror" as const };
    if (defaults.data?.min_duration_seconds)
      return { seconds: defaults.data.min_duration_seconds, source: "default" as const };
    return null;
  }, [feed, defaults.data]);

  const columns = useMemo(
    () => buildColumns({ feedId: feed.id, feedTitle: feed.title, feedKind, minimum, onToggleRow }),
    [feed.id, feed.title, feedKind, minimum, onToggleRow],
  );

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table's instance is not memoized by design
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (item) => item.id,
    state: { rowSelection, expanded, columnVisibility, columnFilters },
    onRowSelectionChange: setRowSelection,
    onExpandedChange: setExpanded,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowCanExpand: () => true,
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: true,
  });

  const tableRows = table.getRowModel().rows;
  const entries = useMemo(() => {
    const list: Entry[] = [];
    for (const row of tableRows) {
      list.push({ key: row.id, row, details: false });
      if (expanded === true || expanded[row.id])
        list.push({ key: `${row.id}:details`, row, details: true });
    }
    return list;
  }, [tableRows, expanded]);

  // The list scrolls with the page; the virtualizer needs to know where the body starts.
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body) return undefined;
    const measure = () =>
      setScrollMargin(Math.round(body.getBoundingClientRect().top + window.scrollY));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    return () => observer.disconnect();
  }, []);

  const virtualizer = useWindowVirtualizer({
    count: entries.length,
    estimateSize: (index) => (entries[index]?.details ? DETAILS_ESTIMATE : ROW_ESTIMATE),
    getItemKey: (index) => entries[index]?.key ?? index,
    overscan: OVERSCAN,
    scrollMargin,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const firstItem = virtualItems[0];
  const lastItem = virtualItems[virtualItems.length - 1];
  const paddingTop = firstItem ? firstItem.start - scrollMargin : 0;
  const paddingBottom = lastItem ? virtualizer.getTotalSize() - (lastItem.end - scrollMargin) : 0;

  // What is on screen decides which pages stay live, and when the next one loads.
  const firstRow = firstItem ? (entries[firstItem.index]?.row.index ?? null) : null;
  const lastRow = lastItem ? (entries[lastItem.index]?.row.index ?? null) : null;
  useEffect(() => {
    if (pages.isPending) return;
    reach(firstRow, lastRow);
  }, [firstRow, lastRow, reach, pages.isPending]);

  const selectedItems = table.getSelectedRowModel().rows.map((row) => row.original);
  const mdUp = useMdUp();
  const columnCount = table
    .getVisibleLeafColumns()
    .filter((column) => mdUp || !HIDE_SMALL.has(column.id)).length;
  const filtered = !!search.q;

  const setSort = (sort: ItemSort) => {
    const order =
      search.sort === sort
        ? search.order === "asc"
          ? "desc"
          : "asc"
        : sort === "title"
          ? "asc"
          : "desc";
    onSearchChange({ sort, order });
  };

  useEffect(() => {
    setRowSelection({});
    lastToggled.current = null;
  }, [search.q, search.sort, search.order]);

  const empty =
    !pages.isPending &&
    rows.length === 0 &&
    !pages.hasMore &&
    !pages.isLoadingMore &&
    !pages.failed;

  return (
    <div className="space-y-3">
      <CatalogToolbar
        feedKind={feedKind}
        q={search.q}
        onQChange={(q) => onSearchChange({ q })}
        columnVisibility={columnVisibility}
        onColumnVisibilityChange={(id, visible) =>
          setColumnVisibility((v) => ({ ...v, [id]: visible }))
        }
      />
      {empty ? (
        <EmptyState
          icon={<ListMusic />}
          title={filtered ? "Nothing matches" : "The Catalog is empty"}
          description={
            filtered
              ? "No title contains that text. Try another filter."
              : feed.kind === "mirror"
                ? "Items appear after the first Refresh lists the Source."
                : "Episodes appear once a URL you added expands or a file is uploaded."
          }
        />
      ) : (
        // The virtualizer keeps the scroll position itself; browser scroll anchoring would fight it.
        <div
          className={cn(
            "overflow-x-auto rounded-lg border [overflow-anchor:none]",
            pages.isPlaceholder && "opacity-70 transition-opacity",
          )}
        >
          {/* Fixed layout: the columns keep their widths whichever rows are rendered. */}
          <Table
            className="min-w-[34rem] table-fixed md:min-w-[60rem]"
            aria-rowcount={pages.isPending || pages.hasMore ? -1 : entries.length + 2}
            aria-busy={pages.isPending || undefined}
          >
            <TableHeader>
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id} aria-rowindex={1}>
                  {headerGroup.headers.map((header) => {
                    const sortKey = SORTABLE[header.column.id];
                    const active = sortKey && search.sort === sortKey;
                    return (
                      <TableHead
                        key={header.id}
                        style={{
                          width:
                            header.column.columnDef.size !== 150
                              ? header.column.columnDef.size
                              : undefined,
                        }}
                        className={cn(
                          HIDE_SMALL.has(header.column.id) && "hidden md:table-cell",
                          header.column.id === "actions" && "text-right",
                        )}
                        aria-sort={
                          active ? (search.order === "asc" ? "ascending" : "descending") : undefined
                        }
                      >
                        {header.isPlaceholder ? null : sortKey ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="-ml-3 h-8"
                            onClick={() => setSort(sortKey)}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {active ? (
                              search.order === "asc" ? (
                                <ArrowUp className="size-3.5" />
                              ) : (
                                <ArrowDown className="size-3.5" />
                              )
                            ) : null}
                          </Button>
                        ) : (
                          flexRender(header.column.columnDef.header, header.getContext())
                        )}
                      </TableHead>
                    );
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody ref={bodyRef}>
              {pages.isPending ? <SkeletonRows columns={columnCount} rows={8} /> : null}
              {paddingTop > 0 ? (
                <tr aria-hidden>
                  <td colSpan={columnCount} style={{ height: paddingTop, padding: 0 }} />
                </tr>
              ) : null}
              {virtualItems.map((virtual) => {
                const entry = entries[virtual.index];
                if (!entry) return null;
                const { row } = entry;
                if (entry.details) {
                  return (
                    <TableRow
                      key={virtual.key}
                      ref={virtualizer.measureElement}
                      data-index={virtual.index}
                      aria-rowindex={virtual.index + 2}
                      className="bg-muted/30 hover:bg-muted/30"
                    >
                      <TableCell colSpan={columnCount} className="px-6 py-4">
                        <RowDetails item={row.original} />
                      </TableCell>
                    </TableRow>
                  );
                }
                return (
                  <CatalogRow
                    key={virtual.key}
                    row={row}
                    cells={row.getVisibleCells()}
                    index={virtual.index}
                    selected={row.getIsSelected()}
                    expanded={row.getIsExpanded()}
                    ignored={isBelowMinimum(row.original, minimum?.seconds)}
                    measure={virtualizer.measureElement}
                  />
                );
              })}
              {paddingBottom > 0 ? (
                <tr aria-hidden>
                  <td colSpan={columnCount} style={{ height: paddingBottom, padding: 0 }} />
                </tr>
              ) : null}
              {pages.isPending ? null : (
                <TableRow
                  aria-rowindex={entries.length + 2}
                  className="hover:bg-transparent"
                  data-testid="catalog-end"
                >
                  <TableCell colSpan={columnCount} className="py-3 text-center">
                    <div
                      role="status"
                      className="inline-flex items-center gap-2 text-muted-foreground"
                    >
                      {pages.failed ? (
                        <>
                          {rows.length ? "More items did not load." : "The Catalog did not load."}
                          <Button variant="outline" size="sm" onClick={pages.retry}>
                            Retry
                          </Button>
                        </>
                      ) : pages.isLoadingMore ? (
                        <>
                          <Loader2 className="size-4 animate-spin" aria-hidden />
                          Loading more…
                        </>
                      ) : pages.hasMore ? (
                        <span className="tabular-nums">
                          {pages.loaded.toLocaleString()} of {pages.total.toLocaleString()} loaded
                        </span>
                      ) : (
                        <span className="tabular-nums">
                          {filtered ? "End of the list" : "End of the Catalog"} ·{" "}
                          {count(rows.length, "item")}
                        </span>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
      <SelectionBar
        items={selectedItems}
        busy={bulk.isPending}
        onArchive={bulk.archiveMany}
        onDelete={bulk.deleteMany}
        onClear={() => setRowSelection({})}
      />
    </div>
  );
}
