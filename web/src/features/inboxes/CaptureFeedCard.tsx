import { Link } from "@tanstack/react-router";
import { ListVideo } from "lucide-react";

import type { MirrorRead } from "@/api/types";
import { HealthDot } from "@/components/common/HealthDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatBytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** A captured YouTube playlist: a synced Mirror shown among the Inboxes. */
export function CaptureFeedCard({ feed }: { feed: MirrorRead }) {
  const counts = feed.counts;
  return (
    <Card data-testid="capture-feed-card">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <ListVideo className="size-4 shrink-0" aria-hidden />
          <Link
            to="/mirrors/$mirrorId"
            params={{ mirrorId: feed.id }}
            className="truncate hover:underline"
          >
            {feed.title}
          </Link>
          <Badge variant="outline" className="ml-auto shrink-0">
            Playlist
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <HealthDot mirror={feed} refreshing={false} withText />
          <span className="tabular-nums">
            {count(counts?.listed ?? feed.episode_count, "video")}
            {counts?.available ? ` · ${counts.available} not downloaded yet` : ""}
            {` · ${formatBytes(feed.storage_bytes)}`}
          </span>
        </div>
        <p className="text-xs">Kept in sync with the playlist; videos download on request.</p>
      </CardContent>
    </Card>
  );
}
