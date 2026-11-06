import { AlertTriangle } from "lucide-react";

import { $api, asError } from "@/api/client";
import { useIsAdmin } from "@/api/session";
import { PageHeader } from "@/components/common/PageHeader";
import { PageSpinner } from "@/components/common/PageSpinner";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { describeWorkerSeen, useWorkerStatus, workerSeenOf } from "./worker-status";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export const REPOSITORY_URL = "https://github.com/flrntdfr/Copycast";

/**
 * The app version, base URL, the User's totals and the worker heartbeat; for Admins also the
 * Engine page: the engine and ffmpeg versions and the Object store layout.
 */
export function AboutPage() {
  const about = $api.useQuery("get", "/api/about");
  const admin = useIsAdmin();
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
        description={
          <>
            Copycast (
            <a
              href={REPOSITORY_URL}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-4 hover:text-foreground"
            >
              GitHub
            </a>
            ) {info.version}
          </>
        }
      />
      <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
        {admin ? (
          <>
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
            <dd data-testid="about-ffmpeg">
              {info.ffmpeg_version ?? (
                <span className="inline-flex items-center gap-1 text-warning-foreground">
                  <AlertTriangle className="size-3.5" aria-hidden /> missing
                </span>
              )}
            </dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Base URL</dt>
        <dd className="font-mono text-xs break-all">{info.base_url}</dd>
        {admin ? (
          <>
            <dt className="text-muted-foreground">Layout version</dt>
            <dd data-testid="about-layout">{info.layout_version}</dd>
          </>
        ) : null}
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
      {admin && !info.ffmpeg_version ? (
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
