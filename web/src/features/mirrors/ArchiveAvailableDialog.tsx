import { Loader2 } from "lucide-react";
import { useState } from "react";

import { $api } from "@/api/client";
import type { MirrorRead } from "@/api/types";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useArchiveAvailable, useInvalidateFeed } from "@/features/feeds/mutations";
import { count } from "@/lib/labels";

/**
 * "Archive everything Available now": queues every Available Episode without touching
 * the policy, and offers to switch the Mirror to Everything so new items follow.
 */
export function ArchiveAvailableDialog({
  mirror,
  open,
  onOpenChange,
}: {
  mirror: MirrorRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const archive = useArchiveAvailable();
  const invalidate = useInvalidateFeed();
  const update = $api.useMutation("patch", "/api/mirrors/{feed_id}", {
    onSuccess: (updated) => invalidate(updated.id),
  });
  const [switchMode, setSwitchMode] = useState(false);
  const available = mirror.counts?.available ?? 0;
  const alreadyEverything = mirror.backfill.mode === "all";
  const pending = archive.isPending || update.isPending;

  const confirm = async () => {
    await archive.mutateAsync({ params: { path: { feed_id: mirror.id } } });
    if (switchMode && !alreadyEverything) {
      await update.mutateAsync({
        params: { path: { feed_id: mirror.id } },
        body: { backfill: { mode: "all" } },
      });
    }
    onOpenChange(false);
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Archive {count(available, "Available Episode")} now?</AlertDialogTitle>
          <AlertDialogDescription>
            Every listed Episode not archived yet is queued at once, whatever the Mirror’s policy;
            items shorter than the minimum length and deleted ones are included. The policy itself
            stays as it is unless you switch it below.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {alreadyEverything ? null : (
          <div className="flex items-start gap-3 rounded-lg border p-3">
            <Checkbox
              id="archive-available-switch"
              checked={switchMode}
              onCheckedChange={(value) => setSwitchMode(value === true)}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="archive-available-switch">
                Also switch this Mirror to Everything
              </Label>
              <p className="text-xs text-muted-foreground">
                So new items are archived as they arrive, and nothing is ever deleted.
              </p>
            </div>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={pending || available === 0}
            onClick={(event) => {
              event.preventDefault();
              void confirm();
            }}
          >
            {pending ? <Loader2 className="animate-spin" /> : null}
            Archive {available.toLocaleString()}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
