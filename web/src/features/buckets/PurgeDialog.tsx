import { Loader2, TriangleAlert } from "lucide-react";

import { $api, describeProblem } from "@/api/client";
import { isBucket, type FeedRead } from "@/api/types";
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
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";
import { useFeedObjects } from "./FeedObjectsTable";
import { useClearFeed } from "./mutations";

/**
 * The bucket's Purge: deletes every Episode that is not locked (`clear_feed`). A dry run counts
 * them when the dialog opens; the files listing says how many files the locks keep.
 */
export function PurgeDialog({
  feed,
  open,
  onOpenChange,
}: {
  feed: FeedRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        {open ? <Body feed={feed} onClose={() => onOpenChange(false)} /> : null}
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
  const objects = useFeedObjects(feed.id);
  const clear = useClearFeed();
  const lockedFiles = (objects.data?.objects ?? []).filter((object) => object.locked === true);
  const matched = preview.data?.matched ?? 0;
  const anyLocked = lockedFiles.length > 0 || (preview.data?.kept_count ?? 0) > 0;
  const counting = preview.isPending || objects.isPending;

  const purge = () =>
    clear
      .mutateAsync({ params: { path: { feed_id: feed.id } }, body: { dry_run: false } })
      .then(onClose)
      // The mutation cache toasts the problem; the dialog stays open to retry.
      .catch(() => undefined);

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle className="flex items-center gap-2">
          {lockedFiles.length > 0 ? (
            <TriangleAlert className="size-5 text-warning" aria-hidden />
          ) : null}
          Purge “{isBucket(feed) ? feed.name : feed.title}”?
        </AlertDialogTitle>
        <AlertDialogDescription asChild>
          <div className="space-y-2" data-testid="purge-summary">
            {counting ? (
              <p className="flex items-center gap-2">
                <Loader2 className="size-4 animate-spin" aria-hidden /> Counting what it holds…
              </p>
            ) : preview.isError ? (
              <p className="text-destructive">{describeProblem(preview.error).title}</p>
            ) : matched === 0 ? (
              <p>
                {anyLocked ? "Nothing to purge: every Episode is locked." : "Nothing to purge."}
              </p>
            ) : (
              <>
                <p>
                  Purging deletes {count(matched, "Episode")} (
                  {formatMegabytes(preview.data?.bytes_freed)}) that {matched === 1 ? "is" : "are"}{" "}
                  not locked.
                </p>
                {lockedFiles.length > 0 ? (
                  <p>
                    {count(lockedFiles.length, "file")} {lockedFiles.length === 1 ? "is" : "are"}{" "}
                    locked and will not be deleted.
                  </p>
                ) : null}
                <p className="font-medium text-foreground">
                  {lockedFiles.length > 0 ? "Delete the others?" : "Delete them?"}
                </p>
              </>
            )}
          </div>
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel disabled={clear.isPending}>No</AlertDialogCancel>
        <Button
          variant="destructive"
          disabled={counting || preview.isError || matched === 0 || clear.isPending}
          onClick={() => void purge()}
        >
          {clear.isPending ? <Loader2 className="animate-spin" /> : null}
          Yes, purge
        </Button>
      </AlertDialogFooter>
    </>
  );
}
