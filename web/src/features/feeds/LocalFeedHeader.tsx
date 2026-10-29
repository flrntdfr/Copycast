import { Link } from "@tanstack/react-router";
import { HardDrive, PaintBucket, Settings2 } from "lucide-react";
import { useState } from "react";

import type { BucketRead } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { FeedUrlField } from "@/components/common/FeedUrlField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AddRequestForm } from "@/features/buckets/AddRequestForm";
import { UploadFilesButton } from "@/features/buckets/UploadFilesDialog";
import { LocalFeedSettingsDialog } from "./LocalFeedSettingsDialog";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** "Autoprune 30 days after the first download", or null when autoprune is off. */
export function describeAutoprune(feed: Pick<BucketRead, "autoprune_days">): string | null {
  return feed.autoprune_days
    ? `Autoprune ${count(feed.autoprune_days, "day")} after the first download`
    : null;
}

/**
 * A Local feed's header: icon or artwork, name, counts and size, autoprune, the feed URL;
 * Upload files…, Files (its bucket) and Settings top right; the Add URL form under it
 * (`initialUrl` prefills it: the Send-to-Local-feed flow lands here).
 */
export function LocalFeedHeader({
  feed,
  initialUrl = "",
}: {
  feed: BucketRead;
  initialUrl?: string;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const autoprune = describeAutoprune(feed);

  return (
    <header className="mb-6 space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        {feed.artwork_url ? (
          <Artwork src={feed.artwork_url} size={96} alt="" className="rounded-lg" />
        ) : (
          <div
            className="flex size-24 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"
            aria-hidden
          >
            <PaintBucket className="size-10" />
          </div>
        )}
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-2xl font-semibold tracking-tight">{feed.name}</h1>
            <Badge variant="secondary" className="font-normal">
              Local
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {count(feed.episode_count, "Episode")} · {formatMegabytes(feed.storage_bytes)}
            {autoprune ? ` · ${autoprune}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1">
          <UploadFilesButton bucket={feed} />
          <Button asChild variant="outline" size="sm">
            <Link to="/buckets/$bucketId" params={{ bucketId: feed.id }}>
              <HardDrive /> Files
            </Link>
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            <Settings2 /> Settings
          </Button>
        </div>
      </div>
      <FeedUrlField url={feed.feed_url} label="Feed URL" className="max-w-2xl" />
      <AddRequestForm bucketId={feed.id} initialUrl={initialUrl} className="max-w-2xl" />
      <LocalFeedSettingsDialog feed={feed} open={settingsOpen} onOpenChange={setSettingsOpen} />
    </header>
  );
}
