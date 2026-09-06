import { Archive, Trash2, X } from "lucide-react";
import { useState } from "react";

import type { ItemRead } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { canArchive, canDelete } from "./catalog-state";
import { count } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** Above this many rows a bulk action asks first. */
export const BULK_CONFIRM_THRESHOLD = 50;

export function SelectionBar({
  items,
  onArchive,
  onDelete,
  onClear,
  busy,
  className,
}: {
  items: ItemRead[];
  onArchive: (items: ItemRead[]) => Promise<void> | void;
  onDelete: (items: ItemRead[]) => Promise<void> | void;
  onClear: () => void;
  busy?: boolean;
  className?: string;
}) {
  const [pending, setPending] = useState<"archive" | "delete" | null>(null);
  const archivable = items.filter(canArchive);
  const deletable = items.filter(canDelete);
  if (items.length === 0) return null;

  const run = async (action: "archive" | "delete") => {
    setPending(null);
    if (action === "archive") await onArchive(archivable);
    else await onDelete(deletable);
    onClear();
  };
  const request = (action: "archive" | "delete") => {
    const affected = action === "archive" ? archivable.length : deletable.length;
    if (action === "delete" || affected > BULK_CONFIRM_THRESHOLD) setPending(action);
    else void run(action);
  };

  return (
    <div
      className={cn(
        "sticky bottom-0 z-20 -mx-4 flex flex-wrap items-center gap-2 border-t bg-background/95 px-4 py-2 backdrop-blur supports-[backdrop-filter]:bg-background/80",
        className,
      )}
      role="region"
      aria-label="Selection"
    >
      <span className="text-sm font-medium tabular-nums">
        {items.length.toLocaleString()} selected
      </span>
      <span className="text-muted-foreground">·</span>
      <Button
        size="sm"
        onClick={() => request("archive")}
        disabled={busy || archivable.length === 0}
      >
        <Archive /> Archive
        {archivable.length && archivable.length !== items.length ? ` ${archivable.length}` : ""}
      </Button>
      <Button
        size="sm"
        variant="outline"
        onClick={() => request("delete")}
        disabled={busy || deletable.length === 0}
      >
        <Trash2 /> Delete
        {deletable.length && deletable.length !== items.length ? ` ${deletable.length}` : ""}
      </Button>
      <Button size="sm" variant="ghost" onClick={onClear} disabled={busy}>
        <X /> Clear
      </Button>
      <AlertDialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pending === "delete"
                ? `Delete ${count(deletable.length, "Episode")}?`
                : `Archive ${count(archivable.length, "Episode")}?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pending === "delete"
                ? "Their media is removed and they leave the published feed. They stay Available and will not re-archive on their own."
                : "Each one becomes a download job for the worker; this can take a while and fill the disk."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={
                pending === "delete"
                  ? "bg-destructive text-white hover:bg-destructive/90"
                  : undefined
              }
              onClick={() => pending && void run(pending)}
            >
              {pending === "delete" ? "Delete" : "Archive"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
