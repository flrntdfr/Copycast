import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { HEALTH_DOT_CLASS, describeHealth, type HealthView } from "@/lib/health";
import type { MirrorRead } from "@/api/types";
import { cn } from "@/lib/utils";

/** The coloured dot; `refreshing` blinks (static under reduced motion). State is always text too. */
export function HealthDot({
  mirror,
  refreshing = false,
  withText = false,
  className,
}: {
  mirror: Parameters<typeof describeHealth>[0];
  refreshing?: boolean;
  withText?: boolean;
  className?: string;
}) {
  const view: HealthView = describeHealth(mirror);
  const dot = (
    <span
      className={cn(
        "inline-block size-2.5 shrink-0 rounded-full",
        HEALTH_DOT_CLASS[view.tone],
        refreshing && "animate-health-blink motion-reduce:animate-none",
      )}
      aria-hidden
    />
  );
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={cn("inline-flex items-center gap-2", className)} data-health={view.status}>
          {dot}
          <span className={cn(!withText && "sr-only", "text-sm")}>
            {refreshing ? "Refreshing…" : view.text}
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{view.detail}</TooltipContent>
    </Tooltip>
  );
}

export type { MirrorRead };
