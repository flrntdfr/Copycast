import { HardDrive } from "lucide-react";

import type { ItemRead } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { OVER_QUOTA_HINT, blockedReasonLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

/**
 * Next to the state of a wanted Catalog item that waits for room (`blocked_reason: "quota"`):
 * "Over quota" in red. It archives on its own once the Owner frees space or gets a larger quota.
 */
export function OverQuotaBadge({ item, className }: { item: ItemRead; className?: string }) {
  const reason = item.blocked_reason ?? null;
  if (reason == null) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className={cn("border-destructive/50 text-destructive", className)}
          data-blocked-reason={reason}
        >
          <HardDrive aria-hidden />
          {blockedReasonLabel(reason)}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{OVER_QUOTA_HINT}</TooltipContent>
    </Tooltip>
  );
}
