import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { HEALTH_DOT_CLASS } from "@/lib/health";
import { cn } from "@/lib/utils";

/**
 * Whether a Feed can reach its storage bucket: green when it can, red when it cannot, grey
 * while unknown. Nothing reports reachability yet, so every caller passes `null` for now.
 */
export function StorageDot({ reachable }: { reachable: boolean | null }) {
  const tone = reachable == null ? "muted" : reachable ? "success" : "destructive";
  const text =
    reachable == null
      ? "Storage not checked"
      : reachable
        ? "Storage reachable"
        : "Storage unreachable";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center" data-storage={tone}>
          <span
            className={cn("inline-block size-2.5 shrink-0 rounded-full", HEALTH_DOT_CLASS[tone])}
            aria-hidden
          />
          <span className="sr-only">{text}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}
