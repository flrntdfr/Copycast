import { Radio } from "lucide-react";

import type { ItemRead } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { LIVE_HINT, LIVE_STATUSES_WITHOUT_RECORDING, liveStatusLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

/**
 * The live-stream badge shown next to the state of a Catalog item that has no recording
 * yet: "Live" (red), "Upcoming", or "Recording being processed" (muted). Nothing for a
 * recorded stream, a plain upload or an RSS item.
 */
export function LiveBadge({ item, className }: { item: ItemRead; className?: string }) {
  const status = item.live_status ?? null;
  if (status == null || !LIVE_STATUSES_WITHOUT_RECORDING.includes(status)) return null;

  const variant =
    status === "is_live" ? "destructive" : status === "is_upcoming" ? "secondary" : "outline";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant={variant}
          className={cn(status === "post_live" && "text-muted-foreground", className)}
          data-live-status={status}
        >
          {status === "is_live" ? <Radio aria-hidden /> : null}
          {liveStatusLabel(status)}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{LIVE_HINT}</TooltipContent>
    </Tooltip>
  );
}
