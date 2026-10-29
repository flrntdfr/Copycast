import { Link } from "@tanstack/react-router";

import { isBucket, type FeedRead, type FeedStorage } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";

/**
 * One Feed's bucket (its `feeds/{id}/` prefix in the Object store) on the Feeds page: the title,
 * then "N files · X MB" from the storage summary. `storage` is undefined while the summary loads
 * ("…" files, the size the Feed reports) and null when it is unavailable (the size only). The
 * card opens the bucket page; with `url` (a Local feed's card while a URL is to be sent) it opens
 * the Local feed's page ready to add that URL.
 */
export function BucketCard({
  feed,
  storage,
  url = "",
}: {
  feed: FeedRead;
  storage?: FeedStorage | null;
  url?: string;
}) {
  const files =
    storage === undefined ? "… files" : storage ? count(storage.file_count, "file") : null;
  const bytes = storage?.total_bytes ?? feed.storage_bytes;
  const card = (
    <Card className="h-full gap-2 py-4 transition-colors hover:bg-accent/40">
      <CardHeader className="px-4">
        <CardTitle className="truncate text-base">
          {isBucket(feed) ? feed.name : feed.title}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 text-sm text-muted-foreground tabular-nums">
        {[files, formatMegabytes(bytes)].filter(Boolean).join(" · ")}
      </CardContent>
    </Card>
  );
  const className =
    "rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";
  return url && isBucket(feed) ? (
    <Link
      to="/feed/$feedId"
      params={{ feedId: feed.id }}
      search={{ url }}
      className={className}
      data-testid="bucket-card"
    >
      {card}
    </Link>
  ) : (
    <Link
      to="/buckets/$bucketId"
      params={{ bucketId: feed.id }}
      className={className}
      data-testid="bucket-card"
    >
      {card}
    </Link>
  );
}
