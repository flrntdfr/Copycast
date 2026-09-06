import { Columns3, Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { RangeArchiveInput } from "./RangeInput";
import { COLUMN_LABELS, TOGGLEABLE_COLUMNS } from "./columns";
import { CATALOG_FACETS, type CatalogFacet } from "./search";
import type { CatalogCounts } from "@/api/types";
import { catalogStateLabel } from "@/lib/labels";

export const FILTER_DEBOUNCE_MS = 300;

export interface CatalogToolbarProps {
  feedId: string;
  feedKind: "mirror" | "inbox";
  q: string;
  facet: CatalogFacet | undefined;
  counts: CatalogCounts | null | undefined;
  onQChange: (q: string) => void;
  onFacetChange: (facet: CatalogFacet | undefined) => void;
  columnVisibility: Record<string, boolean>;
  onColumnVisibilityChange: (id: string, visible: boolean) => void;
  onQueued: () => void;
}

function facetCount(counts: CatalogCounts | null | undefined, facet: CatalogFacet): number | null {
  if (!counts) return null;
  switch (facet) {
    case "listed":
      return counts.listed ?? null;
    case "delisted":
      return counts.delisted ?? null;
    case "available":
      return counts.available ?? null;
    case "queued":
      return counts.wanted ?? null;
    case "failed":
      return counts.failed ?? null;
    default:
      return null;
  }
}

export function CatalogToolbar(props: CatalogToolbarProps) {
  const { q, onQChange } = props;
  const [draft, setDraft] = useState(q);
  const [syncedQ, setSyncedQ] = useState(q);
  if (syncedQ !== q) {
    // The URL changed underneath (back button, cleared facet): adopt it during render.
    setSyncedQ(q);
    setDraft(q);
  }

  useEffect(() => {
    if (draft === q) return undefined;
    const timer = setTimeout(() => onQChange(draft), FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, q, onQChange]);

  const visibleFacets = CATALOG_FACETS.filter((facet) => {
    if (facet === "deleted") return true;
    if (props.feedKind === "inbox" && facet === "delisted") return false;
    return true;
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Label htmlFor="catalog-filter" className="sr-only">
            Filter by title
          </Label>
          <Input
            id="catalog-filter"
            data-filter-input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Filter titles… ( / )"
            className="pr-8 pl-8"
            autoComplete="off"
          />
          {draft ? (
            <Button
              variant="ghost"
              size="icon-xs"
              className="absolute top-1/2 right-1.5 -translate-y-1/2"
              aria-label="Clear filter"
              onClick={() => {
                setDraft("");
                onQChange("");
              }}
            >
              <X />
            </Button>
          ) : null}
        </div>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={props.facet ?? ""}
          onValueChange={(value) =>
            props.onFacetChange(value ? (value as CatalogFacet) : undefined)
          }
          aria-label="Filter by state"
          className="flex-wrap"
        >
          {visibleFacets.map((facet) => {
            const count = facetCount(props.counts, facet);
            return (
              <ToggleGroupItem
                key={facet}
                value={facet}
                aria-label={catalogStateLabel(facet) || "Deleted"}
              >
                {facet === "deleted" ? "Deleted" : catalogStateLabel(facet)}
                {count != null ? (
                  <span className="ml-1 text-muted-foreground tabular-nums">{count}</span>
                ) : null}
              </ToggleGroupItem>
            );
          })}
        </ToggleGroup>
        <div className="ml-auto">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" aria-label="Choose columns">
                <Columns3 /> <span className="hidden sm:inline">Columns</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Columns</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {TOGGLEABLE_COLUMNS.filter(
                (id) => id !== "number" || props.feedKind === "mirror",
              ).map((id) => (
                <DropdownMenuCheckboxItem
                  key={id}
                  checked={props.columnVisibility[id] !== false}
                  onCheckedChange={(checked) => props.onColumnVisibilityChange(id, !!checked)}
                >
                  {COLUMN_LABELS[id]}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <RangeArchiveInput feedId={props.feedId} onQueued={props.onQueued} />
    </div>
  );
}
