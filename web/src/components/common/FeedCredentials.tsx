import { ChevronDown, ChevronRight, KeyRound, RotateCw } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";

import { $api } from "@/api/client";
import type { FeedRead } from "@/api/types";
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
import { CopyField } from "./CopyField";
import { useInvalidateFeed } from "@/features/feeds/mutations";

/**
 * The feed's own username and password (present only while authentication is on),
 * for podcast apps with separate fields such as Overcast, plus the Rotate action.
 */
export function FeedCredentials({
  feed,
  className,
  canRotate = true,
}: {
  feed: FeedRead;
  className?: string;
  /** False where `feed` is a snapshot that would not refresh after a rotation. */
  canRotate?: boolean;
}) {
  const pair = feed.feed_credentials;
  const invalidate = useInvalidateFeed();
  const [confirming, setConfirming] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const rotate = $api.useMutation("post", "/api/feeds/{feed_id}/credentials/rotate", {
    onSuccess: (updated) => {
      invalidate(updated.id);
      setConfirming(false);
      toast.success("Feed credentials rotated", {
        description: "Update every podcast app subscribed to this feed.",
      });
    },
  });
  if (!pair) return null;
  return (
    <div className={className} data-testid="feed-credentials">
      <button
        type="button"
        className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        aria-expanded={expanded}
        aria-controls={detailsId}
        onClick={() => setExpanded((open) => !open)}
      >
        {expanded ? (
          <ChevronDown className="size-4" aria-hidden />
        ) : (
          <ChevronRight className="size-4" aria-hidden />
        )}
        <KeyRound className="size-4" aria-hidden />
        <span className="font-medium text-foreground">Username and password</span>
        <span>
          {expanded
            ? "The feed URL already carries them; apps such as Overcast also take them separately."
            : "Already in the feed URL; expand for apps that ask for them separately."}
        </span>
      </button>
      <div
        id={detailsId}
        hidden={!expanded}
        className="mt-2 grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
      >
        <CopyField label="Username" value={pair.username} />
        <CopyField label="Password" value={pair.password} />
        {canRotate ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="sm:mb-px"
            onClick={() => setConfirming(true)}
          >
            <RotateCw /> Rotate…
          </Button>
        ) : null}
      </div>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rotate the credentials of “{feed.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              A new username and password are minted and the old ones stop working at once. Every
              podcast app subscribed to this feed must be given the new feed URL.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={rotate.isPending}>Keep them</AlertDialogCancel>
            <AlertDialogAction
              disabled={rotate.isPending}
              onClick={(event) => {
                event.preventDefault();
                rotate.mutate({ params: { path: { feed_id: feed.id } } });
              }}
            >
              Rotate
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
