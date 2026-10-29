import { Link } from "@tanstack/react-router";
import { Eraser, PaintBucket, Rss } from "lucide-react";
import { useState } from "react";

import { isBucket, type FeedRead } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";
import { useFeedObjects } from "./FeedObjectsTable";
import { PurgeDialog } from "./PurgeDialog";

/**
 * The bucket page's header for any Feed: icon, name, service, then how many files the bucket
 * holds and their size (the table's own listing). Open feed and Purge top right; every setting
 * lives on the feed page.
 */
export function BucketHeader({ feed }: { feed: FeedRead }) {
  const [purging, setPurging] = useState(false);
  const objects = useFeedObjects(feed.id);
  const local = isBucket(feed);

  return (
    <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start">
      {local ? (
        <div
          className="hidden size-16 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground sm:flex"
          aria-hidden
        >
          <PaintBucket className="size-7" />
        </div>
      ) : (
        <Artwork src={feed.artwork_url} size={64} alt="" className="hidden rounded-lg sm:flex" />
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="truncate text-2xl font-semibold tracking-tight">
            {local ? feed.name : feed.title}
          </h1>
          {local ? (
            <Badge variant="secondary" className="font-normal">
              Local
            </Badge>
          ) : (
            <ServiceBadge service={feed.service} sourceKind={feed.source_kind} />
          )}
        </div>
        <p className="text-sm text-muted-foreground tabular-nums" data-testid="bucket-summary">
          {objects.data
            ? `${count(objects.data.count, "file")} · ${formatMegabytes(objects.data.total_bytes)}`
            : objects.isError
              ? formatMegabytes(feed.storage_bytes)
              : "Counting files…"}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button asChild variant="outline" size="sm">
          <Link to="/feed/$feedId" params={{ feedId: feed.id }}>
            <Rss /> Open feed
          </Link>
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="text-destructive hover:text-destructive"
          onClick={() => setPurging(true)}
        >
          <Eraser /> Purge…
        </Button>
      </div>
      <PurgeDialog feed={feed} open={purging} onOpenChange={setPurging} />
    </header>
  );
}
