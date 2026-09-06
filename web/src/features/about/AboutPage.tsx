import { Link } from "@tanstack/react-router";
import { AlertTriangle, Database } from "lucide-react";

import { $api, asError } from "@/api/client";
import { isMirror } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/common/PageHeader";
import { PageSpinner } from "@/components/common/PageSpinner";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { describeWorkerSeen, useWorkerStatus, workerSeenOf } from "./worker-status";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

/** App and engine versions, ffmpeg, layout, totals, storage per feed, worker heartbeat. */
export function AboutPage() {
  const about = $api.useQuery("get", "/api/about");
  const feeds = $api.useQuery("get", "/api/feeds", {
    params: { query: { sort: "storage_bytes", order: "desc" } },
  });
  const ready = useWorkerStatus();

  if (about.isPending) return <PageSpinner />;
  if (about.isError) throw asError(about.error);
  const info = about.data;
  const totals = info.totals ?? { feeds: 0, episodes: 0, storage_bytes: 0 };
  const feedRows = feeds.data?.feeds ?? [];
  const seen = workerSeenOf(ready.data);
  const workerWarn = seen.kind !== "unknown" && (seen.kind !== "seen" || seen.stale);

  return (
    <>
      <PageHeader title="About" description={`Copycast ${info.version}`} />
      <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
        <dt className="text-muted-foreground">Engine</dt>
        <dd data-testid="about-engine">
          {info.engine.name} {info.engine.version} ({info.engine.channel})
          {info.engine.release_date ? `, released ${formatDate(info.engine.release_date)}` : ""}
          {info.engine.git_head ? (
            <span className="font-mono text-xs text-muted-foreground">
              {" "}
              · {info.engine.git_head}
            </span>
          ) : null}
        </dd>
        <dt className="text-muted-foreground">ffmpeg</dt>
        <dd>
          {info.ffmpeg_version ?? (
            <span className="inline-flex items-center gap-1 text-warning-foreground">
              <AlertTriangle className="size-3.5" aria-hidden /> missing
            </span>
          )}
        </dd>
        <dt className="text-muted-foreground">Base URL</dt>
        <dd className="font-mono text-xs break-all">{info.base_url}</dd>
        <dt className="text-muted-foreground">Layout version</dt>
        <dd>{info.layout_version}</dd>
        <dt className="text-muted-foreground">Totals</dt>
        <dd>
          {formatNumber(totals.feeds)} feeds · {formatNumber(totals.episodes)} Episodes ·{" "}
          {formatBytes(totals.storage_bytes)}
        </dd>
        <dt className="text-muted-foreground">Worker last seen</dt>
        <dd data-testid="about-worker" className={cn(workerWarn && "text-warning-foreground")}>
          {ready.isPending ? (
            "…"
          ) : ready.isError ? (
            "unknown (readiness unavailable)"
          ) : seen.kind === "seen" ? (
            <>
              <RelativeTime value={seen.at} />
              {seen.stale ? " (not running?)" : ""}
            </>
          ) : (
            describeWorkerSeen(seen)
          )}
        </dd>
      </dl>
      {!info.ffmpeg_version ? (
        <Alert variant="destructive" className="mt-6">
          <AlertTriangle />
          <AlertTitle>ffmpeg is missing</AlertTitle>
          <AlertDescription>
            Downloads cannot be processed until ffmpeg is installed next to the worker.
          </AlertDescription>
        </Alert>
      ) : null}
      {workerWarn ? (
        <Alert className="mt-6">
          <AlertTriangle />
          <AlertTitle>The worker has not reported recently</AlertTitle>
          <AlertDescription>
            Refreshes and downloads wait until a worker is running; feeds keep being served.
          </AlertDescription>
        </Alert>
      ) : null}
      <h2 className="mt-8 mb-3 text-sm font-medium">Storage per feed</h2>
      {feeds.isSuccess && feedRows.length === 0 ? (
        <EmptyState icon={<Database />} title="Nothing archived yet" />
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Feed</TableHead>
                <TableHead className="hidden sm:table-cell">Kind</TableHead>
                <TableHead className="text-right">Episodes</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead className="hidden md:table-cell">Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {feeds.isPending ? <SkeletonRows columns={5} rows={3} /> : null}
              {feeds.isError ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-destructive">
                    The feed list could not be loaded.
                  </TableCell>
                </TableRow>
              ) : null}
              {feedRows.map((feed) => (
                <TableRow key={feed.id} data-testid="storage-row">
                  <TableCell className="max-w-xs truncate">
                    {isMirror(feed) ? (
                      <Link
                        to="/mirrors/$mirrorId"
                        params={{ mirrorId: feed.id }}
                        className="hover:underline"
                      >
                        {feed.title}
                      </Link>
                    ) : (
                      <Link
                        to="/inboxes/$inboxId"
                        params={{ inboxId: feed.id }}
                        className="hover:underline"
                      >
                        {feed.title}
                      </Link>
                    )}
                  </TableCell>
                  <TableCell className="hidden sm:table-cell">
                    {isMirror(feed) ? "Mirror" : "Inbox"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatNumber(feed.episode_count)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatBytes(feed.storage_bytes)}
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <RelativeTime value={feed.created_at} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  );
}
