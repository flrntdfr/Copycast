import type { FeedRead } from "@/api/types";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Badge } from "@/components/ui/badge";
import { syncView, type SyncTone } from "@/lib/sync";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<SyncTone, string> = {
  success: "bg-success/15 text-success",
  warning: "bg-warning/20 text-warning-foreground dark:bg-warning/15 dark:text-warning",
};

/**
 * "Synced 3 hours ago" in green when the Feed is healthy, orange otherwise; "Never synced" when
 * it never was. For a Bucket Feed, pass the time of its last Request (see `syncView`).
 */
export function SyncBadge({
  feed,
  lastRequestAt = null,
  className,
}: {
  feed: FeedRead;
  lastRequestAt?: string | null;
  className?: string;
}) {
  const view = syncView(feed, lastRequestAt);
  return (
    <Badge className={cn(TONE_CLASS[view.tone], className)} data-sync={view.tone}>
      {view.at ? (
        <>
          Synced <RelativeTime value={view.at} />
        </>
      ) : view.tone === "success" ? (
        "Synced"
      ) : (
        "Never synced"
      )}
    </Badge>
  );
}
