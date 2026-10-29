import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, ArrowUpDown, PaintBucket, Radio } from "lucide-react";

import { $api } from "@/api/client";
import { isMirror, type BucketRead, type FeedRead, type MirrorRead } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { EmptyState } from "@/components/common/EmptyState";
import { HealthDot } from "@/components/common/HealthDot";
import { RelativeTime } from "@/components/common/RelativeTime";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { BucketRowActions, MirrorRowActions } from "./MirrorRowActions";
import { useRunningRefreshFeedIds } from "@/features/jobs/useRunningJobs";
import { formatNumber } from "@/lib/format";
import { HEALTH_DOT_CLASS } from "@/lib/health";
import { cn } from "@/lib/utils";

export type MirrorsSort = "title" | "created_at" | "updated_at";

export interface MirrorsTableProps {
  sort: MirrorsSort;
  order: "asc" | "desc";
  onSort: (sort: MirrorsSort, order: "asc" | "desc") => void;
}

function SortHeader({
  label,
  column,
  sort,
  order,
  onSort,
  className,
}: {
  label: string;
  column: MirrorsSort;
  className?: string;
} & MirrorsTableProps) {
  const active = sort === column;
  const Icon = active ? (order === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <TableHead
      className={className}
      aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}
    >
      <Button
        variant="ghost"
        size="sm"
        className="-ml-3 h-8"
        onClick={() => onSort(column, active && order === "asc" ? "desc" : "asc")}
      >
        {label} <Icon className="size-3.5 text-muted-foreground" />
      </Button>
    </TableHead>
  );
}

/** The playlist captures are listed with the Buckets, below the table. */
function inTable(feed: FeedRead): boolean {
  return !(isMirror(feed) && feed.playlist_capture);
}

function MirrorRow({ mirror, refreshing }: { mirror: MirrorRead; refreshing: boolean }) {
  return (
    <TableRow data-testid="mirror-row">
      <TableCell>
        <HealthDot mirror={mirror} refreshing={refreshing} />
      </TableCell>
      <TableCell>
        <Artwork src={mirror.artwork_url} size={36} />
      </TableCell>
      <TableCell className="max-w-[18rem] md:max-w-sm">
        <Link
          to="/mirrors/$mirrorId"
          params={{ mirrorId: mirror.id }}
          className="block truncate font-medium hover:underline"
        >
          {mirror.title}
        </Link>
        <div className="flex items-center gap-2 text-xs text-muted-foreground md:hidden">
          <span>{formatNumber(mirror.episode_count)} Episodes</span>
          {mirror.paused ? <Badge variant="outline">Paused</Badge> : null}
        </div>
        {mirror.paused ? (
          <Badge variant="outline" className="mt-1 hidden md:inline-flex">
            Paused
          </Badge>
        ) : null}
      </TableCell>
      <TableCell className="hidden md:table-cell">
        <ServiceBadge service={mirror.service} sourceKind={mirror.source_kind} />
      </TableCell>
      <TableCell className="hidden text-right tabular-nums md:table-cell">
        {formatNumber(mirror.episode_count)}
      </TableCell>
      <TableCell className="hidden text-right tabular-nums lg:table-cell">
        {formatNumber(mirror.counts?.available ?? 0)}
      </TableCell>
      <TableCell className="hidden lg:table-cell">
        {mirror.paused ? (
          <span className="text-muted-foreground">Paused</span>
        ) : (
          <RelativeTime
            value={mirror.last_refresh_success_at ?? mirror.last_refresh_attempt_at}
            fallback="Never"
          />
        )}
      </TableCell>
      <TableCell>
        <div className="hidden md:block">
          <MirrorRowActions mirror={mirror} />
        </div>
        <div className="md:hidden">
          <MirrorRowActions mirror={mirror} compact />
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * A Bucket Feed has no Source: service "Local", no Available count, no Refresh. Its dot is
 * green once it holds Episodes, grey while empty.
 */
function BucketRow({ bucket }: { bucket: BucketRead }) {
  return (
    <TableRow data-testid="bucket-row">
      <TableCell>
        <span className="inline-flex items-center" data-health="local">
          <span
            className={cn(
              "inline-block size-2.5 shrink-0 rounded-full",
              HEALTH_DOT_CLASS[bucket.episode_count > 0 ? "success" : "muted"],
            )}
            aria-hidden
          />
          <span className="sr-only">Local</span>
        </span>
      </TableCell>
      <TableCell>
        <div
          className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
          aria-hidden
        >
          <PaintBucket className="size-4" />
        </div>
      </TableCell>
      <TableCell className="max-w-[18rem] md:max-w-sm">
        <Link
          to="/buckets/$bucketId"
          params={{ bucketId: bucket.id }}
          className="block truncate font-medium hover:underline"
        >
          {bucket.name}
        </Link>
        <div className="text-xs text-muted-foreground md:hidden">
          {formatNumber(bucket.episode_count)} Episodes
        </div>
      </TableCell>
      <TableCell className="hidden md:table-cell">
        <Badge variant="secondary" className="font-normal">
          Local
        </Badge>
      </TableCell>
      <TableCell className="hidden text-right tabular-nums md:table-cell">
        {formatNumber(bucket.episode_count)}
      </TableCell>
      <TableCell className="hidden text-right text-muted-foreground lg:table-cell">—</TableCell>
      <TableCell className="hidden text-muted-foreground lg:table-cell">—</TableCell>
      <TableCell>
        <BucketRowActions bucket={bucket} />
      </TableCell>
    </TableRow>
  );
}

/** Every Feed but the playlist captures: the Mirrors and, as "Local" rows, the Bucket Feeds. */
export function MirrorsTable(props: MirrorsTableProps) {
  const { sort, order } = props;
  const query = $api.useQuery("get", "/api/feeds", { params: { query: { sort, order } } });
  const refreshing = useRunningRefreshFeedIds();
  const feeds = (query.data?.feeds ?? []).filter(inTable);

  if (query.isSuccess && feeds.length === 0) {
    return (
      <EmptyState
        icon={<Radio />}
        title="No Feeds yet"
        description="Paste a podcast feed URL, a page advertising one, or a YouTube channel above to create your first Mirror."
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8">
              <span className="sr-only">Health</span>
            </TableHead>
            <TableHead className="w-12">
              <span className="sr-only">Artwork</span>
            </TableHead>
            <SortHeader label="Title" column="title" {...props} />
            <TableHead className="hidden md:table-cell">Service</TableHead>
            <TableHead className="hidden text-right md:table-cell">Episodes</TableHead>
            <TableHead className="hidden text-right lg:table-cell">Available</TableHead>
            <SortHeader
              label="Last Refresh"
              column="updated_at"
              className="hidden lg:table-cell"
              {...props}
            />
            <TableHead className="w-32 text-right">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {query.isPending ? <SkeletonRows columns={8} /> : null}
          {feeds.map((feed) =>
            isMirror(feed) ? (
              <MirrorRow key={feed.id} mirror={feed} refreshing={refreshing.has(feed.id)} />
            ) : (
              <BucketRow key={feed.id} bucket={feed} />
            ),
          )}
        </TableBody>
      </Table>
    </div>
  );
}
