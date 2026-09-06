import { keepPreviousData } from "@tanstack/react-query";
import {
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
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ListMusic } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

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
import { catalogStateOf } from "./catalog-state";
import { useBulkActions } from "./mutations";
import { CATALOG_PAGE_SIZE, type CatalogSearch, toListItemsQuery } from "./search";
import { cn } from "@/lib/utils";

const COLUMNS_STORAGE_KEY = "copycast.catalog.columns";

/** Which header sorts by which `list_items` sort key. */
const SORTABLE: Partial<Record<string, ItemSort>> = {
  number: "ordinal",
  title: "title",
  published: "published",
};

function readColumnVisibility(): VisibilityState {
  try {
    const raw = window.localStorage.getItem(COLUMNS_STORAGE_KEY);
    if (raw) return JSON.parse(raw) as VisibilityState;
  } catch {
    // ignore
  }
  return {};
}

export interface CatalogTableProps {
  feed: FeedRead;
  search: CatalogSearch;
  onSearchChange: (patch: Partial<CatalogSearch>) => void;
}

export function CatalogTable({ feed, search, onSearchChange }: CatalogTableProps) {
  const feedKind = feed.kind === "mirror" ? "mirror" : "inbox";
  const query = toListItemsQuery(search);
  const items = $api.useQuery(
    "get",
    "/api/feeds/{feed_id}/items",
    { params: { path: { feed_id: feed.id }, query } },
    { placeholderData: keepPreviousData },
  );
  const bulk = useBulkActions(feed.id, feedKind);

  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(readColumnVisibility);
  const [columnFilters] = useState<ColumnFiltersState>([]);
  const lastToggled = useRef<number | null>(null);

  useEffect(() => {
    try {
      window.localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(columnVisibility));
    } catch {
      // ignore
    }
  }, [columnVisibility]);

  // Rows that are hidden unless the tombstone facet is on.
  const rows = useMemo(() => {
    const all = items.data?.items ?? [];
    if (search.state === "deleted") return all;
    return all.filter((item) => catalogStateOf(item) !== "hidden");
  }, [items.data, search.state]);

  const onToggleRow = useCallback(
    (row: Row<ItemRead>, checked: boolean, shiftKey: boolean) => {
      const index = row.index;
      setRowSelection((current) => {
        const next = { ...current };
        const from =
          shiftKey && lastToggled.current != null ? Math.min(lastToggled.current, index) : index;
        const to =
          shiftKey && lastToggled.current != null ? Math.max(lastToggled.current, index) : index;
        for (let i = from; i <= to; i += 1) {
          const id = rows[i]?.id;
          if (!id) continue;
          if (checked) next[id] = true;
          else delete next[id];
        }
        return next;
      });
      lastToggled.current = index;
    },
    [rows],
  );

  const columns = useMemo(
    () => buildColumns({ feedId: feed.id, feedTitle: feed.title, feedKind, onToggleRow }),
    [feed.id, feed.title, feedKind, onToggleRow],
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

  const selectedItems = table.getSelectedRowModel().rows.map((row) => row.original);
  const total = items.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / CATALOG_PAGE_SIZE));
  const first = total === 0 ? 0 : (search.page - 1) * CATALOG_PAGE_SIZE + 1;
  const last = Math.min(total, search.page * CATALOG_PAGE_SIZE);

  const setSort = (sort: ItemSort) => {
    const order =
      search.sort === sort
        ? search.order === "asc"
          ? "desc"
          : "asc"
        : sort === "title"
          ? "asc"
          : "desc";
    onSearchChange({ sort, order, page: 1 });
  };

  useEffect(() => {
    setRowSelection({});
    lastToggled.current = null;
  }, [search.page, search.state, search.q, search.sort, search.order]);

  return (
    <div className="space-y-3">
      <CatalogToolbar
        feedId={feed.id}
        feedKind={feedKind}
        q={search.q}
        facet={search.state}
        counts={feed.kind === "mirror" ? feed.counts : null}
        onQChange={(q) => onSearchChange({ q, page: 1 })}
        onFacetChange={(facet) => onSearchChange({ state: facet, page: 1 })}
        columnVisibility={columnVisibility}
        onColumnVisibilityChange={(id, visible) =>
          setColumnVisibility((v) => ({ ...v, [id]: visible }))
        }
        onQueued={() => void items.refetch()}
      />
      {items.isSuccess && rows.length === 0 ? (
        <EmptyState
          icon={<ListMusic />}
          title={search.q || search.state ? "Nothing matches" : "The Catalog is empty"}
          description={
            search.q || search.state
              ? "Try another filter or clear the state facet."
              : feed.kind === "mirror"
                ? "Items appear after the first Refresh lists the Source."
                : "Items appear once a Request expands."
          }
        />
      ) : (
        <div
          className={cn(
            "overflow-x-auto rounded-lg border",
            items.isFetching && !items.isPending && "opacity-70 transition-opacity",
          )}
        >
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => {
                    const sortKey = SORTABLE[header.column.id];
                    const active = sortKey && search.sort === sortKey;
                    const hideSmall = ["published", "duration", "size", "downloads"].includes(
                      header.column.id,
                    );
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
                          hideSmall && "hidden md:table-cell",
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
            <TableBody>
              {items.isPending ? (
                <SkeletonRows columns={table.getVisibleLeafColumns().length} rows={8} />
              ) : null}
              {table.getRowModel().rows.map((row) => (
                <Fragment key={row.id}>
                  <TableRow
                    data-state={row.getIsSelected() ? "selected" : undefined}
                    data-testid="catalog-row"
                    data-item-id={row.original.id}
                    className="cursor-pointer"
                    onClick={() => row.toggleExpanded()}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const hideSmall = ["published", "duration", "size", "downloads"].includes(
                        cell.column.id,
                      );
                      return (
                        <TableCell
                          key={cell.id}
                          className={cn(
                            hideSmall && "hidden md:table-cell",
                            cell.column.id === "title" && "max-w-[14rem] md:max-w-md",
                          )}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                  {row.getIsExpanded() ? (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={row.getVisibleCells().length} className="px-6 py-4">
                        <RowDetails item={row.original} />
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
        <span className="tabular-nums">
          {total
            ? `${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()}`
            : ""}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Previous page"
            disabled={search.page <= 1}
            onClick={() => onSearchChange({ page: search.page - 1 })}
          >
            <ChevronLeft />
          </Button>
          <span className="tabular-nums">
            {search.page} / {pageCount}
          </span>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Next page"
            disabled={search.page >= pageCount}
            onClick={() => onSearchChange({ page: search.page + 1 })}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>
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
