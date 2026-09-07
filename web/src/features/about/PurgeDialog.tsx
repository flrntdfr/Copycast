import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";
import type { PruneResult } from "@/api/types";
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
import { formatBytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** The trash button next to the totals: a dry run first, then every archived Episode goes. */
export function PurgeButton() {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<PruneResult | null>(null);
  const queryClient = useQueryClient();
  const purge = $api.useMutation("post", "/api/admin/purge", { meta: { silent: true } });

  /** Opening the dialog runs the dry run; closing it forgets the numbers. */
  const setOpenAndPreview = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setPreview(null);
      return;
    }
    purge.mutate({ body: { dry_run: true } }, { onSuccess: (result) => setPreview(result) });
  };

  const confirm = () => {
    purge.mutate(
      { body: { dry_run: false } },
      {
        onSuccess: (result) => {
          void queryClient.invalidateQueries({ queryKey: [OPS.about[0], OPS.about[1]] });
          void queryClient.invalidateQueries({ queryKey: [OPS.list_feeds[0], OPS.list_feeds[1]] });
          toast.success(
            `${count(result.deleted_count, "Episode")} deleted, ${formatBytes(result.bytes_freed)} freed`,
          );
          setOpen(false);
        },
        onError: () => toast.error("The purge failed"),
      },
    );
  };

  const deleting = purge.isPending && preview !== null;
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Delete every archived Episode"
            onClick={() => setOpenAndPreview(true)}
          >
            <Trash2 className="text-destructive" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Delete every archived Episode; feeds stay</TooltipContent>
      </Tooltip>
      <AlertDialog open={open} onOpenChange={setOpenAndPreview}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {preview
                ? `Delete ${count(preview.matched, "archived Episode")} (${formatBytes(preview.bytes_freed)})?`
                : "Delete every archived Episode?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              Every feed keeps its Catalog, artwork, credentials and settings; only the downloaded
              media goes. Deleted Episodes leave Tombstones: Everything, Rolling and Follow never
              download them again on their own, Automatic Mirrors do when a podcast app asks, and
              you can archive any of them again on purpose.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Keep everything</AlertDialogCancel>
            <AlertDialogAction
              disabled={preview === null || preview.matched === 0 || deleting}
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={(event) => {
                event.preventDefault();
                confirm();
              }}
            >
              {deleting ? <Loader2 className="animate-spin" /> : null}
              Delete {preview ? preview.matched.toLocaleString() : ""}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
