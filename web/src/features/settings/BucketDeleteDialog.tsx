import { Loader2, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { $api } from "@/api/client";
import { isInbox, type FeedRead } from "@/api/types";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useDeleteFeed } from "@/features/feeds/mutations";
import { count } from "@/lib/labels";
import { formatMegabytes } from "@/lib/format";
import { useClearFeed } from "./mutations";

/**
 * The trash button's confirmation. A dry run of clearing the bucket says what is flagged
 * Archive: with nothing flagged it is the plain "delete the Feed" question; with flagged
 * Episodes it warns and offers to delete only the rest. The default Inbox is never deleted,
 * only emptied.
 */
export function BucketDeleteDialog({
  feed,
  onClose,
}: {
  feed: FeedRead | null;
  onClose: () => void;
}) {
  return (
    <AlertDialog open={feed !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <AlertDialogContent>
        {feed ? <Body feed={feed} onClose={onClose} /> : null}
      </AlertDialogContent>
    </AlertDialog>
  );
}

function Body({ feed, onClose }: { feed: FeedRead; onClose: () => void }) {
  const preview = $api.useQuery(
    "post",
    "/api/feeds/{feed_id}/clear",
    { params: { path: { feed_id: feed.id } }, body: { dry_run: true } },
    { staleTime: 0, gcTime: 0 },
  );
  const clear = useClearFeed();
  const { deleteFeed, isPending: deletingFeed } = useDeleteFeed();
  const [error, setError] = useState<string | null>(null);
  const busy = clear.isPending || deletingFeed;
  const fixed = isInbox(feed) && feed.is_default;
  const noun = isInbox(feed) ? "Inbox" : "Mirror";
  const kept = preview.data?.kept_count ?? 0;
  const rest = preview.data?.matched ?? 0;
  const warn = kept > 0;

  const run = (action: () => Promise<unknown>) => {
    setError(null);
    action()
      .then(onClose)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Delete failed"));
  };
  const clearRest = () =>
    run(() =>
      clear.mutateAsync({ params: { path: { feed_id: feed.id } }, body: { dry_run: false } }),
    );
  const deleteAll = () => run(() => deleteFeed(feed, { navigateAway: false }));

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle className="flex items-center gap-2">
          {warn ? <TriangleAlert className="size-5 text-warning" aria-hidden /> : null}
          {fixed ? `Empty the Inbox “${feed.title}”?` : `Delete ${noun} “${feed.title}”?`}
        </AlertDialogTitle>
        <AlertDialogDescription asChild>
          <div className="space-y-2">
            {preview.isPending ? (
              <p className="flex items-center gap-2">
                <Loader2 className="size-4 animate-spin" /> Counting what it holds…
              </p>
            ) : warn ? (
              <>
                <p data-testid="bucket-archive-warning">
                  This bucket holds {count(kept, "Episode")} flagged Archive (
                  {formatMegabytes(preview.data?.kept_bytes)}) that Copycast keeps for good, and may
                  be the only copy.
                </p>
                <p>
                  {rest > 0
                    ? `Delete the rest removes the other ${count(rest, "Episode")} (${formatMegabytes(preview.data?.bytes_freed)}) and keeps the feed.`
                    : "Nothing else is archived in it."}
                  {fixed ? null : " Delete everything removes the feed and every Episode in it."}
                </p>
              </>
            ) : fixed ? (
              <p>
                The default Inbox cannot be deleted. Emptying it removes its{" "}
                {count(rest, "Episode")} ({formatMegabytes(preview.data?.bytes_freed)}) and keeps
                the feed.
              </p>
            ) : (
              <p>
                This removes {count(feed.episode_count ?? 0, "archived Episode")} (
                {formatMegabytes(feed.storage_bytes)}) and the published feed. Podcast apps
                subscribed to it will stop working. This cannot be undone.
              </p>
            )}
            {error ? <p className="text-destructive">{error}</p> : null}
          </div>
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel disabled={busy}>Keep it</AlertDialogCancel>
        {warn || fixed ? (
          <Button
            variant={fixed && !warn ? "destructive" : "outline"}
            disabled={busy || preview.isPending || rest === 0}
            onClick={clearRest}
          >
            {clear.isPending ? <Loader2 className="animate-spin" /> : null}
            {fixed && !warn ? "Empty the Inbox" : "Delete the rest"}
          </Button>
        ) : null}
        {fixed ? null : (
          <Button variant="destructive" disabled={busy || preview.isPending} onClick={deleteAll}>
            {deletingFeed ? <Loader2 className="animate-spin" /> : null}
            {warn ? "Delete everything" : `Delete ${noun}`}
          </Button>
        )}
      </AlertDialogFooter>
    </>
  );
}
