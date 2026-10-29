import type { BucketRead } from "@/api/types";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BucketSettings } from "./BucketSettings";

/** The Bucket's settings (name, autoprune, Prune now…, Danger zone) in a dialog. */
export function BucketSettingsDialog({
  bucket,
  open,
  onOpenChange,
}: {
  bucket: BucketRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Bucket settings</DialogTitle>
          <DialogDescription>{bucket.name}</DialogDescription>
        </DialogHeader>
        <BucketSettings bucket={bucket} />
      </DialogContent>
    </Dialog>
  );
}
