import { AlertTriangle, ExternalLink, FileDown } from "lucide-react";

import { $api, asError } from "@/api/client";
import { PageHeader } from "@/components/common/PageHeader";
import { PageSpinner } from "@/components/common/PageSpinner";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { describeWorkerSeen, useWorkerStatus, workerSeenOf } from "./worker-status";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export const REPOSITORY_URL = "https://github.com/flrntdfr/Copycast";

/** App and engine versions, ffmpeg, layout, totals, worker heartbeat. */
export function AboutPage() {
  const about = $api.useQuery("get", "/api/about");
  const ready = useWorkerStatus();

  if (about.isPending) return <PageSpinner />;
  if (about.isError) throw asError(about.error);
  const info = about.data;
  const totals = info.totals ?? { feeds: 0, episodes: 0, storage_bytes: 0 };
  const seen = workerSeenOf(ready.data);
  const workerWarn = seen.kind !== "unknown" && (seen.kind !== "seen" || seen.stale);

  return (
    <>
      <PageHeader
        title="About"
        description={`Copycast ${info.version}`}
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <a
                href="/api/feeds.opml"
                download="copycast.opml"
                title="Every Mirror and Bucket feed with its credentials, to subscribe to all of them at once in Overcast, Pocket Casts or AntennaPod"
              >
                <FileDown /> Export OPML
              </a>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <a href={REPOSITORY_URL} target="_blank" rel="noreferrer">
                <ExternalLink /> GitHub
              </a>
            </Button>
          </>
        }
      />
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
    </>
  );
}
