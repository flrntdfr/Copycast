import { Link } from "@tanstack/react-router";
import { PaintBucket, MoreHorizontal, Scissors, Settings2, Trash2 } from "lucide-react";
import { useState } from "react";

import type { BucketRead } from "@/api/types";
import { FeedCredentials } from "@/components/common/FeedCredentials";
import { FeedUrlField } from "@/components/common/FeedUrlField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AddRequestForm } from "./AddRequestForm";
import { PruneDialog } from "./PruneDialog";
import { UploadFilesButton } from "./UploadFilesDialog";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { formatBytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** "Autoprune 30 days after first download" / "No autoprune". */
export function describeAutoprune(bucket: Pick<BucketRead, "autoprune_days">): string {
  return bucket.autoprune_days
    ? `Autoprune ${count(bucket.autoprune_days, "day")} after the first download`
    : "No autoprune";
}

/** Name, counts and size, the Bucket Feed URL, the Add URL form and the actions menu. */
export function BucketHeader({
  bucket,
  initialUrl = "",
}: {
  bucket: BucketRead;
  initialUrl?: string;
}) {
  const [pruning, setPruning] = useState(false);
  const [deleting, setDeleting] = useState(false);
  return (
    <header className="mb-6 space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div
          className="hidden size-16 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground sm:flex"
          aria-hidden
        >
          <PaintBucket className="size-7" />
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{bucket.name}</h1>
            <Badge variant="outline">Bucket</Badge>
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {count(bucket.episode_count, "Episode")} · {count(bucket.request_count, "Request")} ·{" "}
            {formatBytes(bucket.storage_bytes)} · {describeAutoprune(bucket)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <UploadFilesButton bucket={bucket} />
          <Button variant="outline" size="sm" onClick={() => setPruning(true)}>
            <Scissors /> Prune now…
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="More actions">
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link
                  to="/buckets/$bucketId"
                  params={{ bucketId: bucket.id }}
                  search={{ tab: "settings" }}
                >
                  <Settings2 /> Settings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(true)}>
                <Trash2 /> Delete…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          <FeedUrlField url={bucket.feed_url} label="Bucket Feed URL" />
          <FeedCredentials feed={bucket} />
        </div>
        <AddRequestForm bucketId={bucket.id} initialUrl={initialUrl} />
      </div>
      <PruneDialog bucketId={bucket.id} open={pruning} onOpenChange={setPruning} />
      <DeleteFeedDialog feed={bucket} open={deleting} onOpenChange={setDeleting} />
    </header>
  );
}
