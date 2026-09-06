import { Link } from "@tanstack/react-router";
import {
  AlertTriangle,
  ExternalLink,
  Link2,
  MoreHorizontal,
  Pause,
  Play,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import type { MirrorRead } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { FeedCredentials } from "@/components/common/FeedCredentials";
import { FeedUrlField } from "@/components/common/FeedUrlField";
import { HealthDot } from "@/components/common/HealthDot";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { useRequestRefresh, useSetPaused } from "@/features/feeds/mutations";
import { formatBytes, formatNumber } from "@/lib/format";
import { count } from "@/lib/labels";

/** Artwork, title, Service, Source link, health, counts and the feed URL; Refresh / Pause / menu. */
export function MirrorHeader({ mirror, refreshing }: { mirror: MirrorRead; refreshing: boolean }) {
  const refresh = useRequestRefresh();
  const { setPaused, isPending: pausing } = useSetPaused();
  const [deleting, setDeleting] = useState(false);
  const counts = mirror.counts;
  const failing = mirror.health.status === "error" && (mirror.last_error || mirror.health.reason);

  return (
    <header className="mb-6 space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <Artwork src={mirror.artwork_url} size={96} alt="" className="rounded-lg" />
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{mirror.title}</h1>
            <ServiceBadge service={mirror.service} sourceKind={mirror.source_kind} />
            {mirror.paused ? <Badge variant="outline">Paused</Badge> : null}
          </div>
          <a
            href={mirror.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex max-w-full items-center gap-1 truncate font-mono text-xs text-muted-foreground hover:underline"
          >
            <span className="truncate">{mirror.source_url}</span>
            <ExternalLink className="size-3 shrink-0" aria-hidden />
          </a>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <HealthDot mirror={mirror} refreshing={refreshing} withText />
            <span className="text-muted-foreground tabular-nums">
              {count(mirror.episode_count ?? 0, "Episode")}
              {counts ? ` · ${formatNumber(counts.available ?? 0)} Available` : ""}
              {counts?.delisted ? ` · ${formatNumber(counts.delisted)} Delisted` : ""}
              {counts?.wanted ? ` · ${formatNumber(counts.wanted)} queued` : ""}
              {counts?.failed ? ` · ${formatNumber(counts.failed)} failed` : ""}
              {` · ${formatBytes(mirror.storage_bytes)}`}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refresh.mutate({ params: { path: { feed_id: mirror.id } } })}
            disabled={refresh.isPending}
          >
            <RefreshCw className={refresh.isPending || refreshing ? "animate-spin" : undefined} />{" "}
            Refresh now
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void setPaused(mirror.id, !mirror.paused)}
            disabled={pausing}
          >
            {mirror.paused ? <Play /> : <Pause />} {mirror.paused ? "Resume" : "Pause"}
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
                  to="/mirrors/$mirrorId"
                  params={{ mirrorId: mirror.id }}
                  search={{ tab: "settings" }}
                >
                  <Link2 /> Change Source URL…
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
      {failing ? (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>The last Refresh failed</AlertTitle>
          <AlertDescription className="break-words">
            {mirror.last_error ?? mirror.health.reason}
          </AlertDescription>
        </Alert>
      ) : null}
      <FeedUrlField url={mirror.feed_url} label="Mirror Feed URL" className="max-w-2xl" />
      <FeedCredentials feed={mirror} className="max-w-2xl" />
      <DeleteFeedDialog feed={mirror} open={deleting} onOpenChange={setDeleting} />
    </header>
  );
}
