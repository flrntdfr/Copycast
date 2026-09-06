import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copyWithToast } from "@/lib/copy";
import { cn } from "@/lib/utils";

/** A read-only feed URL with a Copy button; what people paste into their podcast app. */
export function FeedUrlField({
  url,
  label = "Feed URL",
  className,
  hideLabel = false,
}: {
  url: string;
  label?: string;
  className?: string;
  hideLabel?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const id = `feed-url-${url.replace(/[^a-z0-9]/gi, "").slice(-16)}`;

  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id} className={cn(hideLabel && "sr-only")}>
        {label}
      </Label>
      <div className="flex gap-2">
        <Input
          id={id}
          readOnly
          value={url}
          onFocus={(event) => event.currentTarget.select()}
          className="font-mono text-xs"
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label={`Copy ${label.toLowerCase()}`}
              onClick={() => {
                void copyWithToast(url, label).then(() => setCopied(true));
              }}
            >
              {copied ? <Check className="text-success" /> : <Copy />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>Copy {label.toLowerCase()}</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}
