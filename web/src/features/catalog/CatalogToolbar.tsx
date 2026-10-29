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
import { COLUMN_LABELS, TOGGLEABLE_COLUMNS } from "./columns";

export const FILTER_DEBOUNCE_MS = 300;

export interface CatalogToolbarProps {
  feedKind: "mirror" | "bucket";
  q: string;
  onQChange: (q: string) => void;
  columnVisibility: Record<string, boolean>;
  onColumnVisibilityChange: (id: string, visible: boolean) => void;
}

/** The title filter (debounced into the URL) and the Columns menu. */
export function CatalogToolbar(props: CatalogToolbarProps) {
  const { q, onQChange } = props;
  const [draft, setDraft] = useState(q);
  const [syncedQ, setSyncedQ] = useState(q);
  if (syncedQ !== q) {
    // The URL changed underneath (back button): adopt it during render.
    setSyncedQ(q);
    setDraft(q);
  }

  useEffect(() => {
    if (draft === q) return undefined;
    const timer = setTimeout(() => onQChange(draft), FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, q, onQChange]);

  return (
    <div className="flex items-center gap-2">
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
            {TOGGLEABLE_COLUMNS.filter((id) => id !== "number" || props.feedKind === "mirror").map(
              (id) => (
                <DropdownMenuCheckboxItem
                  key={id}
                  checked={props.columnVisibility[id] !== false}
                  onCheckedChange={(checked) => props.onColumnVisibilityChange(id, !!checked)}
                >
                  {COLUMN_LABELS[id]}
                </DropdownMenuCheckboxItem>
              ),
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
