import { Ghost } from "lucide-react";

import type { ItemRead } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { catalogStateOf, isTombstone } from "./catalog-state";
import { formatPercent } from "@/lib/format";
import { TOMBSTONE_HINT, catalogStateHint, catalogStateLabel } from "@/lib/labels";
import { useItemProgress } from "@/stores/progress";
import { cn } from "@/lib/utils";

type Variant = "default" | "secondary" | "destructive" | "outline";

/** State text (always text, never colour alone) with the tombstone icon and live percent. */
export function StateBadge({ item, className }: { item: ItemRead; className?: string }) {
  const state = catalogStateOf(item);
  const live = useItemProgress(item.id);
  const percent = state === "archiving" ? (live?.progress.percent ?? null) : null;

  if (state === "hidden") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            className={cn("text-muted-foreground", className)}
            data-state="hidden"
          >
            <Ghost aria-hidden /> Deleted
          </Badge>
        </TooltipTrigger>
        <TooltipContent>{TOMBSTONE_HINT}; no longer listed by the Source.</TooltipContent>
      </Tooltip>
    );
  }

  let variant: Variant = "outline";
  let extraClass = "";
  let hint: string = catalogStateHint[state];
  let label = catalogStateLabel(state);
  let icon: React.ReactNode = null;

  switch (state) {
    case "listed":
      variant = "secondary";
      break;
    case "delisted":
      extraClass = "border-warning bg-warning/15 text-warning-foreground";
      hint = "No longer listed by the Source; kept in the Mirror Feed";
      break;
    case "available":
      if (isTombstone(item)) {
        icon = <Ghost aria-hidden />;
        hint = TOMBSTONE_HINT;
      }
      break;
    case "queued":
      break;
    case "archiving":
      variant = "default";
      if (percent != null) label = `${label} ${formatPercent(percent)}`;
      break;
    case "failed":
      variant = "destructive";
      hint =
        [
          item.attempt_count
            ? `${item.attempt_count} ${item.attempt_count === 1 ? "attempt" : "attempts"}`
            : null,
          item.last_error,
        ]
          .filter(Boolean)
          .join(": ") || hint;
      if (item.attempt_count) label = `${label} (${item.attempt_count})`;
      break;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={variant} className={cn(extraClass, className)} data-state={state}>
          {icon}
          {label}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{hint}</TooltipContent>
    </Tooltip>
  );
}
