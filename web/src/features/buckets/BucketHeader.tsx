import { Link } from "@tanstack/react-router";
import { ExternalLink, PaintBucket, Settings2 } from "lucide-react";
import { useState } from "react";

import { $api } from "@/api/client";
import { isBucket, type BucketRead, type FeedRead, type MirrorRead } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { SyncBadge } from "@/components/common/SyncBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MirrorSettingsDialog } from "@/features/mirrors/MirrorSettingsDialog";
import { AddRequestForm } from "./AddRequestForm";
import { BucketSettingsDialog } from "./BucketSettingsDialog";
import { UploadFilesButton } from "./UploadFilesDialog";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** "Autoprune 30 days after first download" / "No autoprune". */
export function describeAutoprune(bucket: Pick<BucketRead, "autoprune_days">): string {
  return bucket.autoprune_days
    ? `Autoprune ${count(bucket.autoprune_days, "day")} after the first download`
    : "No autoprune";
}

/**
 * The storage view's header for any Feed: icon, name, service, sync badge, counts and size; the
 * Settings dialog top right (Upload files too for a Bucket Feed, Open Mirror for a Mirror) and,
 * for a Bucket Feed only, the Add URL form (how URLs get in; the Send-to-Bucket flow lands here).
 */
export function BucketHeader({ feed, initialUrl = "" }: { feed: FeedRead; initialUrl?: string }) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const bucket: BucketRead | null = isBucket(feed) ? feed : null;
  const mirror: MirrorRead | null = isBucket(feed) ? null : feed;
  const lastRequest = $api.useQuery(
    "get",
    "/api/buckets/{bucket_id}/requests",
    { params: { path: { bucket_id: feed.id }, query: { limit: 1 } } },
    { enabled: bucket !== null && bucket.request_count > 0 },
  );
  const lastRequestAt = lastRequest.data?.requests[0]?.created_at ?? null;

  return (
    <header className="mb-6 space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        {bucket ? (
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
              {bucket ? bucket.name : feed.title}
            </h1>
            {mirror ? (
              <ServiceBadge service={mirror.service} sourceKind={mirror.source_kind} />
            ) : (
              <Badge variant="secondary" className="font-normal">
                Local
              </Badge>
            )}
            <SyncBadge feed={feed} lastRequestAt={lastRequestAt} />
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {count(feed.episode_count, "Episode")} · {formatMegabytes(feed.storage_bytes)}
            {bucket ? ` · ${describeAutoprune(bucket)}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {bucket ? (
            <UploadFilesButton bucket={bucket} />
          ) : (
            <Button asChild variant="outline" size="sm">
              <Link to="/mirrors/$mirrorId" params={{ mirrorId: feed.id }}>
                <ExternalLink /> Open Mirror
              </Link>
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setSettingsOpen(true)}>
            <Settings2 /> Settings
          </Button>
        </div>
      </div>
      {bucket ? (
        <>
          <AddRequestForm bucketId={bucket.id} initialUrl={initialUrl} />
          <BucketSettingsDialog
            bucket={bucket}
            open={settingsOpen}
            onOpenChange={setSettingsOpen}
          />
        </>
      ) : null}
      {mirror ? (
        <MirrorSettingsDialog mirror={mirror} open={settingsOpen} onOpenChange={setSettingsOpen} />
      ) : null}
    </header>
  );
}
