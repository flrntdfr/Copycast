import { useState } from "react";

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
import { useDeleteFeed } from "./mutations";
import type { FeedRead } from "@/api/types";
import { count } from "@/lib/labels";
import { formatBytes } from "@/lib/format";

/** The "only copy" confirmation for deleting a Feed and everything archived in it. */
export function DeleteFeedDialog({
  feed,
  open,
  onOpenChange,
  navigateAway = true,
}: {
  feed: FeedRead | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  navigateAway?: boolean;
}) {
  const { deleteFeed, isPending } = useDeleteFeed();
  const [error, setError] = useState<string | null>(null);
  if (!feed) return null;
  const noun = feed.kind === "mirror" ? "Mirror" : "Inbox";
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Delete {noun} “{feed.title}”?
          </AlertDialogTitle>
          <AlertDialogDescription>
            This removes {count(feed.episode_count ?? 0, "archived Episode")} (
            {formatBytes(feed.storage_bytes)}) and the published feed. Copycast may hold the only
            copy of this content; podcast apps subscribed to it will stop working. This cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Keep it</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-white hover:bg-destructive/90"
            disabled={isPending}
            onClick={(event) => {
              event.preventDefault();
              setError(null);
              deleteFeed(feed, { navigateAway })
                .then(() => onOpenChange(false))
                .catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : "Delete failed"),
                );
            }}
          >
            Delete {noun}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
