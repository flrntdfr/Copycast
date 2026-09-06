import { useConnectionStatus, type ConnectionStatus } from "@/api/events";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const WORDING: Record<ConnectionStatus, { text: string; className: string }> = {
  open: { text: "Live updates connected", className: "bg-success" },
  connecting: { text: "Connecting to live updates", className: "bg-muted-foreground/50" },
  reconnecting: { text: "Live updates interrupted; polling every 10 s", className: "bg-warning" },
};

/** Shows the SSE connection state; amber while reconnecting (polling). */
export function ConnectionDot() {
  const status = useConnectionStatus();
  const { text, className } = WORDING[status];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="inline-flex size-8 items-center justify-center"
          role="status"
          data-connection={status}
        >
          <span className={cn("size-2.5 rounded-full", className)} aria-hidden />
          <span className="sr-only">{text}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}
