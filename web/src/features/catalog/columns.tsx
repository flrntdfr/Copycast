import type { ColumnDef, Row } from "@tanstack/react-table";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { ItemRead } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { RowActions } from "./RowActions";
import { StateBadge } from "./StateBadge";
import { catalogStateOf, displayNumber } from "./catalog-state";
import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
} from "@/lib/format";
import { cn } from "@/lib/utils";

export interface CatalogColumnContext {
  feedId: string;
  feedTitle: string;
  feedKind: "mirror" | "inbox";
  /** Shift-click range selection needs the last row toggled. */
  onToggleRow: (row: Row<ItemRead>, checked: boolean, shiftKey: boolean) => void;
}

export const COLUMN_LABELS: Record<string, string> = {
  number: "#",
  title: "Title",
  published: "Published",
  duration: "Duration",
  size: "Size",
  downloads: "Downloads",
  state: "State",
};

/** Columns that can be hidden from the toolbar; `select`, `title`, `state` and `actions` stay. */
export const TOGGLEABLE_COLUMNS = ["number", "published", "duration", "size", "downloads"] as const;

export function buildColumns(ctx: CatalogColumnContext): ColumnDef<ItemRead>[] {
  const columns: ColumnDef<ItemRead>[] = [
    {
      id: "select",
      enableHiding: false,
      header: ({ table }) => (
        <Checkbox
          checked={
            table.getIsAllPageRowsSelected()
              ? true
              : table.getIsSomePageRowsSelected()
                ? "indeterminate"
                : false
          }
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
          aria-label="Select all on this page"
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          onClick={(event) => {
            event.stopPropagation();
            ctx.onToggleRow(row, !row.getIsSelected(), event.shiftKey);
          }}
          onCheckedChange={() => undefined}
          aria-label={`Select ${row.original.title}`}
        />
      ),
      size: 32,
    },
  ];

  if (ctx.feedKind === "mirror") {
    columns.push({
      id: "number",
      accessorFn: (item) => displayNumber(item).value,
      header: "#",
      cell: ({ row }) => {
        const { value, source } = displayNumber(row.original);
        return (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-muted-foreground tabular-nums">{value}</span>
            </TooltipTrigger>
            <TooltipContent>
              {source
                ? `Source number ${value} (Ordinal ${row.original.ordinal})`
                : `Ordinal ${value}; the Source does not number its items`}
            </TooltipContent>
          </Tooltip>
        );
      },
      size: 56,
    });
  }

  columns.push(
    {
      id: "title",
      accessorKey: "title",
      enableHiding: false,
      header: "Title",
      cell: ({ row }) => {
        const item = row.original;
        const state = catalogStateOf(item);
        const sub =
          state === "delisted"
            ? "Delisted; kept in the Mirror Feed"
            : state === "failed"
              ? (item.last_error ?? "The last attempt failed")
              : state === "hidden"
                ? "Deleted by you and no longer listed"
                : null;
        return (
          <div className="flex min-w-0 items-start gap-1">
            <Button
              variant="ghost"
              size="icon-xs"
              className="mt-0.5 shrink-0"
              aria-expanded={row.getIsExpanded()}
              aria-label={row.getIsExpanded() ? "Hide details" : "Show details"}
              onClick={(event) => {
                event.stopPropagation();
                row.toggleExpanded();
              }}
            >
              {row.getIsExpanded() ? <ChevronDown /> : <ChevronRight />}
            </Button>
            <div className="min-w-0">
              <div
                className={cn(
                  "truncate font-medium",
                  state === "hidden" && "text-muted-foreground line-through",
                )}
                title={item.title}
              >
                {item.title || <span className="text-muted-foreground italic">Untitled</span>}
              </div>
              {sub ? (
                <div
                  className={cn(
                    "truncate text-xs",
                    state === "failed" ? "text-destructive" : "text-muted-foreground",
                  )}
                  title={sub}
                >
                  {sub}
                </div>
              ) : null}
            </div>
          </div>
        );
      },
    },
    {
      id: "published",
      accessorKey: "published_at",
      header: "Published",
      cell: ({ row }) => (
        <span title={formatDateTime(row.original.published_at)} className="whitespace-nowrap">
          {formatDate(row.original.published_at)}
        </span>
      ),
      size: 110,
    },
    {
      id: "duration",
      accessorKey: "duration_seconds",
      header: "Duration",
      cell: ({ row }) => (
        <span className="tabular-nums">{formatDuration(row.original.duration_seconds)}</span>
      ),
      size: 80,
    },
    {
      id: "size",
      accessorFn: (item) => item.media?.bytes ?? null,
      header: "Size",
      cell: ({ row }) => (
        <span className="tabular-nums">{formatBytes(row.original.media?.bytes)}</span>
      ),
      size: 80,
    },
    {
      id: "downloads",
      accessorKey: "download_count",
      header: "Downloads",
      cell: ({ row }) => (
        <span className="tabular-nums">
          {row.original.media ? formatNumber(row.original.download_count) : ""}
        </span>
      ),
      size: 90,
    },
    {
      id: "state",
      accessorFn: (item) => catalogStateOf(item),
      enableHiding: false,
      header: "State",
      cell: ({ row }) => <StateBadge item={row.original} />,
      size: 110,
    },
    {
      id: "actions",
      enableHiding: false,
      header: () => <span className="sr-only">Actions</span>,
      cell: ({ row }) => <RowActions item={row.original} feedTitle={ctx.feedTitle} />,
      size: 120,
    },
  );
  return columns;
}
