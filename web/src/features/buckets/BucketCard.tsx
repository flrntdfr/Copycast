import { Link } from "@tanstack/react-router";

import { $api } from "@/api/client";
import { isBucket, type FeedRead } from "@/api/types";
import { SyncBadge } from "@/components/common/SyncBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";

/**
 * One Feed's bucket (its `feeds/{id}/` prefix in the Object store) on the Feeds page: the title,
 * then the sync badge, the Episode count and the size. A Bucket Feed's badge dates from its last
 * Request; with `url`, a Bucket Feed opens ready to add that URL (a Mirror never carries it).
 */
export function BucketCard({ feed, url = "" }: { feed: FeedRead; url?: string }) {
  const last = $api.useQuery(
    "get",
    "/api/buckets/{bucket_id}/requests",
    { params: { path: { bucket_id: feed.id }, query: { limit: 1 } } },
    { enabled: isBucket(feed) && feed.request_count > 0 },
  );
  const lastRequestAt = last.data?.requests[0]?.created_at ?? null;
  return (
    <Link
      to="/buckets/$bucketId"
      params={{ bucketId: feed.id }}
      search={url && isBucket(feed) ? { url } : {}}
      className="rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      data-testid="bucket-card"
    >
      <Card className="h-full gap-2 py-4 transition-colors hover:bg-accent/40">
        <CardHeader className="px-4">
          <CardTitle className="truncate text-base">
            {isBucket(feed) ? feed.name : feed.title}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-x-1.5 gap-y-1 px-4 text-sm text-muted-foreground">
          <SyncBadge feed={feed} lastRequestAt={lastRequestAt} />
          <span className="tabular-nums">({count(feed.episode_count, "Episode")})</span>
          <span aria-hidden>·</span>
          <span className="tabular-nums">{formatMegabytes(feed.storage_bytes)}</span>
        </CardContent>
      </Card>
    </Link>
  );
}
