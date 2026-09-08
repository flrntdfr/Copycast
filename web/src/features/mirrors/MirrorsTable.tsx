import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, ArrowUpDown, Radio } from "lucide-react";

import { $api } from "@/api/client";
import { isMirror, type MirrorRead } from "@/api/types";
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
import { MirrorRowActions } from "./MirrorRowActions";
import { useRunningRefreshFeedIds } from "@/features/jobs/useRunningJobs";
import { formatBytes, formatNumber } from "@/lib/format";

export type MirrorsSort = "title" | "created_at" | "updated_at" | "storage_bytes";

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

export function MirrorsTable(props: MirrorsTableProps) {
  const { sort, order } = props;
  const query = $api.useQuery("get", "/api/feeds", {
    params: { query: { kind: "mirror", sort, order } },
  });
  const refreshing = useRunningRefreshFeedIds();
  // Playlist captures live on the Inboxes screen.
  const mirrors: MirrorRead[] = (query.data?.feeds ?? [])
    .filter(isMirror)
    .filter((feed) => !feed.playlist_capture);

  if (query.isSuccess && mirrors.length === 0) {
    return (
      <EmptyState
        icon={<Radio />}
        title="No Mirrors yet"
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
              label="Size"
              column="storage_bytes"
              className="hidden text-right md:table-cell"
              {...props}
            />
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
          {query.isPending ? <SkeletonRows columns={9} /> : null}
          {mirrors.map((mirror) => (
            <TableRow key={mirror.id} data-testid="mirror-row">
              <TableCell>
                <HealthDot mirror={mirror} refreshing={refreshing.has(mirror.id)} />
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
                  <span>·</span>
                  <span>{formatBytes(mirror.storage_bytes)}</span>
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
              <TableCell className="hidden text-right tabular-nums md:table-cell">
                {formatBytes(mirror.storage_bytes)}
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
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
