import { QrCode } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** A button that shows `value` as a QR code, only while the dialog is open. */
export function QrCodeButton({ value, label }: { value: string; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={`Show ${label.toLowerCase()} as a QR code`}
            onClick={() => setOpen(true)}
          >
            <QrCode />
          </Button>
        </TooltipTrigger>
        <TooltipContent>QR code</TooltipContent>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
            <DialogDescription>
              Scan it with your phone to subscribe; the code carries the feed’s own credentials.
            </DialogDescription>
          </DialogHeader>
          {open ? <QrCodeImage value={value} /> : null}
          <p className="font-mono text-xs break-all text-muted-foreground">{value}</p>
        </DialogContent>
      </Dialog>
    </>
  );
}

function QrCodeImage({ value }: { value: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    QRCode.toString(value, { type: "svg", errorCorrectionLevel: "M", margin: 1 })
      .then((markup) => {
        if (!cancelled) setSvg(markup);
      })
      .catch(() => {
        if (!cancelled) setSvg(null);
      });
    return () => {
      cancelled = true;
    };
  }, [value]);
  if (!svg) return <div className="aspect-square w-full animate-pulse rounded-lg bg-muted" />;
  return (
    <div
      role="img"
      aria-label="QR code"
      data-testid="qr-code"
      className="mx-auto w-full max-w-64 rounded-lg bg-white p-2 [&>svg]:h-auto [&>svg]:w-full"
      // The markup is generated locally from the URL; nothing user-authored is injected.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
