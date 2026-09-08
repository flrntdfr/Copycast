import { Download, FileText, Pause, Play, Plus, RotateCcw, Trash2 } from "lucide-react";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useArchiveItem, useDeleteItem, useFetchMetadata } from "./mutations";
import { canArchive, canDelete } from "./catalog-state";
import { play, useIsPlaying } from "@/stores/player";

function Action({
  label,
  onClick,
  disabled,
  children,
  asChild,
}: {
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  children: React.ReactNode;
  asChild?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
          asChild={asChild}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** Play/Download/Delete for Episodes; Add or Retry for everything archivable. */
export function RowActions({ item, feedTitle }: { item: ItemRead; feedTitle: string }) {
  const archive = useArchiveItem(item.feed_id);
  const remove = useDeleteItem(item.feed_id);
  const fetchMetadata = useFetchMetadata(item.feed_id);
  const [confirming, setConfirming] = useState(false);
  const playing = useIsPlaying(item.id);

  return (
    <div
      className="flex items-center justify-end gap-0.5"
      onClick={(event) => event.stopPropagation()}
      role="presentation"
    >
      {item.media ? (
        <>
          <Action
            label={playing ? "Pause" : "Play"}
            onClick={() =>
              play({
                itemId: item.id,
                feedId: item.feed_id,
                title: item.title,
                feedTitle,
                url: item.media?.url ?? "",
                mime: item.media?.mime ?? "",
                artworkUrl: item.artwork_url ?? null,
                durationSeconds: item.duration_seconds ?? null,
                chaptersUrl:
                  item.assets?.find((asset) => asset.kind === "chapters" && asset.url)?.url ?? null,
              })
            }
          >
            {playing ? <Pause /> : <Play />}
          </Action>
          <Action label="Download" asChild>
            <a href={item.media.url} download>
              <Download />
            </a>
          </Action>
        </>
      ) : null}
      {!item.description && item.item_url ? (
        <Action
          label="Fetch description"
          onClick={() =>
            fetchMetadata.mutate({
              params: { path: { feed_id: item.feed_id, item_id: item.id } },
            })
          }
          disabled={fetchMetadata.isPending}
        >
          <FileText className={fetchMetadata.isPending ? "animate-pulse" : undefined} />
        </Action>
      ) : null}
      {canArchive(item) ? (
        <Action
          label={item.state === "failed" ? "Retry" : "Archive"}
          onClick={() =>
            archive.mutate({ params: { path: { feed_id: item.feed_id, item_id: item.id } } })
          }
          disabled={archive.isPending}
        >
          {item.state === "failed" ? <RotateCcw /> : <Plus />}
        </Action>
      ) : null}
      {canDelete(item) ? (
        <>
          <Action label="Delete" onClick={() => setConfirming(true)} disabled={remove.isPending}>
            <Trash2 />
          </Action>
          <AlertDialog open={confirming} onOpenChange={setConfirming}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete “{item.title}”?</AlertDialogTitle>
                <AlertDialogDescription>
                  The archived media is removed and the item leaves the published feed. It stays
                  Available in the Catalog and will not be archived again unless you ask.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep it</AlertDialogCancel>
                <AlertDialogAction
                  className="bg-destructive text-white hover:bg-destructive/90"
                  onClick={() =>
                    remove.mutate({ params: { path: { feed_id: item.feed_id, item_id: item.id } } })
                  }
                >
                  Delete
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      ) : null}
    </div>
  );
}
