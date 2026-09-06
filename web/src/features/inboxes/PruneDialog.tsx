import { Loader2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { $api, describeProblem } from "@/api/client";
import type { PruneRequest, PruneResult } from "@/api/types";
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
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useInvalidateCatalog } from "@/features/catalog/mutations";
import { formatBytes } from "@/lib/format";
import { count } from "@/lib/labels";

export const PRUNE_DRY_RUN_DEBOUNCE_MS = 400;

export interface PruneCriteria {
  downloaded: boolean;
  olderThanDays: number | null;
}

export function toPruneRequest(criteria: PruneCriteria, dryRun: boolean): PruneRequest {
  const body: PruneRequest = { downloaded: criteria.downloaded, dry_run: dryRun };
  if (criteria.olderThanDays != null) body.older_than_days = criteria.olderThanDays;
  return body;
}

export function criteriaUsable(criteria: PruneCriteria): boolean {
  return criteria.downloaded || criteria.olderThanDays != null;
}

/** "Deletes 12 Episodes (1.2 GB)" wording for a dry run. */
export function describePrune(result: PruneResult): string {
  if (result.matched === 0) return "Nothing matches these criteria.";
  return `Deletes ${count(result.matched, "Episode")} (${formatBytes(result.bytes_freed)})`;
}

/** Prune an Inbox on demand: criteria, a debounced dry-run preview, then a confirmation. */
export function PruneDialog({
  inboxId,
  open,
  onOpenChange,
}: {
  inboxId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const ids = useId();
  const invalidate = useInvalidateCatalog(inboxId);
  const [downloaded, setDownloaded] = useState(true);
  const [olderEnabled, setOlderEnabled] = useState(false);
  const [olderDays, setOlderDays] = useState("30");
  const [previewFor, setPreviewFor] = useState<{ key: string; result: PruneResult } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const days = Number.parseInt(olderDays, 10);
  const criteria: PruneCriteria = {
    downloaded,
    olderThanDays: olderEnabled && Number.isInteger(days) && days >= 0 ? days : null,
  };
  const usable = criteriaUsable(criteria);
  const key = JSON.stringify(criteria);
  /** The preview counts only while it describes the current criteria. */
  const preview = previewFor?.key === key ? previewFor.result : null;

  const dryRun = $api.useMutation("post", "/api/inboxes/{inbox_id}/prune", {
    meta: { silent: true },
  });
  const close = () => {
    setConfirming(false);
    setPreviewFor(null);
    onOpenChange(false);
  };
  const prune = $api.useMutation("post", "/api/inboxes/{inbox_id}/prune", {
    onSuccess: (result) => {
      invalidate();
      toast.success(`Deleted ${count(result.deleted_count, "Episode")}`, {
        description: `${formatBytes(result.bytes_freed)} freed`,
      });
      close();
    },
  });
  const { mutateAsync: runDryRun, reset: resetDryRun } = dryRun;

  useEffect(() => {
    if (!open) return undefined;
    if (!usable) {
      resetDryRun();
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      runDryRun({
        params: { path: { inbox_id: inboxId } },
        body: toPruneRequest(JSON.parse(key) as PruneCriteria, true),
      })
        .then((result) => {
          if (!cancelled) setPreviewFor({ key, result });
        })
        .catch(() => undefined);
    }, PRUNE_DRY_RUN_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, key, usable, inboxId, runDryRun, resetDryRun]);

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Prune this Inbox</DialogTitle>
          <DialogDescription>
            Delete Episodes matching every criterion below. Deleted Episodes leave the Inbox Feed;
            their Requests stay.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <Checkbox
              id={`${ids}-downloaded`}
              checked={downloaded}
              onCheckedChange={(value) => setDownloaded(value === true)}
              className="mt-0.5"
            />
            <Label
              htmlFor={`${ids}-downloaded`}
              className="flex flex-col items-start gap-0.5 font-normal"
            >
              <span className="font-medium">Downloaded at least once</span>
              <span className="text-xs text-muted-foreground">
                Episodes a podcast app has fetched.
              </span>
            </Label>
          </div>
          <div className="flex items-start gap-3">
            <Checkbox
              id={`${ids}-older`}
              checked={olderEnabled}
              onCheckedChange={(value) => setOlderEnabled(value === true)}
              className="mt-0.5"
            />
            <div className="flex flex-1 flex-col gap-2">
              <Label
                htmlFor={`${ids}-older`}
                className="flex flex-col items-start gap-0.5 font-normal"
              >
                <span className="font-medium">Added more than N days ago</span>
                <span className="text-xs text-muted-foreground">
                  Counted from the date the Request added the Episode.
                </span>
              </Label>
              {olderEnabled ? (
                <div className="flex items-center gap-2">
                  <Label htmlFor={`${ids}-days`} className="sr-only">
                    Days
                  </Label>
                  <Input
                    id={`${ids}-days`}
                    type="number"
                    min={0}
                    className="w-24"
                    value={olderDays}
                    onChange={(event) => setOlderDays(event.target.value)}
                    aria-invalid={!Number.isInteger(days) || days < 0}
                  />
                  <span className="text-sm text-muted-foreground">days</span>
                </div>
              ) : null}
            </div>
          </div>
          <p
            className="min-h-5 text-sm"
            role="status"
            aria-live="polite"
            data-testid="prune-preview"
          >
            {!usable ? (
              <span className="text-muted-foreground">Pick at least one criterion.</span>
            ) : dryRun.isError ? (
              <span className="text-destructive">{describeProblem(dryRun.error).title}</span>
            ) : preview ? (
              describePrune(preview)
            ) : (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <Loader2 className="size-3 animate-spin" aria-hidden /> Counting…
              </span>
            )}
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!preview || preview.matched === 0 || prune.isPending}
            onClick={() => setConfirming(true)}
          >
            Delete{preview?.matched ? ` ${preview.matched.toLocaleString()}` : ""}
          </Button>
        </DialogFooter>
        <AlertDialog open={confirming} onOpenChange={setConfirming}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{preview ? describePrune(preview) : "Delete"}?</AlertDialogTitle>
              <AlertDialogDescription>
                The media is removed now; this cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep them</AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-white hover:bg-destructive/90"
                onClick={() =>
                  prune.mutate({
                    params: { path: { inbox_id: inboxId } },
                    body: toPruneRequest(criteria, false),
                  })
                }
              >
                {prune.isPending ? <Loader2 className="animate-spin" /> : null}
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}
