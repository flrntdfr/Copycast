import type { MirrorRead } from "@/api/types";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MirrorSettingsForm } from "./MirrorSettingsForm";

/** The Mirror's settings form in a dialog, opened from the header's Settings button. */
export function MirrorSettingsDialog({
  mirror,
  open,
  onOpenChange,
}: {
  mirror: MirrorRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Mirror settings</DialogTitle>
          <DialogDescription>{mirror.title}</DialogDescription>
        </DialogHeader>
        <MirrorSettingsForm mirror={mirror} />
      </DialogContent>
    </Dialog>
  );
}
