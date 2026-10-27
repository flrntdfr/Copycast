import { Link } from "@tanstack/react-router";

import type { MirrorRead } from "@/api/types";
import { HealthDot } from "@/components/common/HealthDot";
import { RelativeTime } from "@/components/common/RelativeTime";
import { StorageDot } from "@/components/common/StorageDot";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { count } from "@/lib/labels";

/** A captured YouTube playlist among the Inboxes: storage dot, name, sync status, Episode count. */
export function CaptureFeedCard({ feed }: { feed: MirrorRead }) {
  return (
    <Link
      to="/mirrors/$mirrorId"
      params={{ mirrorId: feed.id }}
      className="rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      data-testid="capture-feed-card"
    >
      <Card className="h-full gap-2 py-4 transition-colors hover:bg-accent/40">
        <CardHeader className="px-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <StorageDot reachable={null} />
            <span className="truncate">{feed.title}</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-2 px-4 text-sm text-muted-foreground">
          <span className="flex min-w-0 items-center gap-2">
            <HealthDot mirror={feed} />
            <span className="truncate">
              Synced{" "}
              <RelativeTime
                value={feed.last_refresh_success_at ?? feed.last_refresh_attempt_at}
                fallback="never"
              />
            </span>
          </span>
          <span className="shrink-0 tabular-nums">{count(feed.episode_count, "Episode")}</span>
        </CardContent>
      </Card>
    </Link>
  );
}
