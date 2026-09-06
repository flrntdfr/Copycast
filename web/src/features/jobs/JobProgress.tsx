import { CheckCircle2, CircleX, Loader2, XCircle } from "lucide-react";

import type { JobRead } from "@/api/types";
import { Progress } from "@/components/ui/progress";
import { formatBytes, formatEta, formatPercent, formatSpeed } from "@/lib/format";
import { errorKindLabel, jobKindLabel, jobStatusLabel, progressPhaseLabel } from "@/lib/labels";
import { useJobProgress } from "@/stores/progress";
import { cn } from "@/lib/utils";

/** Status + live progress of one job; the percent comes from `progress` SSE frames. */
export function JobProgress({
  job,
  className,
  compact = false,
}: {
  job: JobRead;
  className?: string;
  compact?: boolean;
}) {
  const live = useJobProgress(job.id);
  const progress = live?.progress ?? job.progress ?? null;
  const percent = progress?.percent ?? null;
  const running = job.status === "running";
  const phase = progress ? progressPhaseLabel(progress.phase) : null;

  const statusIcon =
    job.status === "succeeded" ? (
      <CheckCircle2 className="size-4 text-success" aria-hidden />
    ) : job.status === "failed" ? (
      <XCircle className="size-4 text-destructive" aria-hidden />
    ) : job.status === "cancelled" ? (
      <CircleX className="size-4 text-muted-foreground" aria-hidden />
    ) : (
      <Loader2 className={cn("size-4", running && "animate-spin")} aria-hidden />
    );

  const detail: string[] = [];
  if (running && phase) detail.push(phase);
  if (running && progress?.downloaded_bytes != null) {
    detail.push(
      progress.total_bytes
        ? `${formatBytes(progress.downloaded_bytes)} of ${formatBytes(progress.total_bytes)}`
        : formatBytes(progress.downloaded_bytes),
    );
  }
  if (running && progress?.speed_bps) detail.push(formatSpeed(progress.speed_bps));
  if (running && progress?.eta_seconds != null)
    detail.push(`${formatEta(progress.eta_seconds)} left`);
  if (job.status === "failed" && job.error) detail.push(job.error);
  if (job.status === "failed" && job.error_kind) detail.push(errorKindLabel(job.error_kind));

  return (
    <div className={cn("flex flex-col gap-1.5", className)} data-job-status={job.status}>
      <div className="flex items-center gap-2 text-sm">
        {statusIcon}
        <span className="font-medium">
          {jobKindLabel(job.kind)} · {jobStatusLabel(job.status)}
          {running && percent != null ? ` ${formatPercent(percent)}` : ""}
        </span>
        {job.status === "queued" && job.attempt > 0 ? (
          <span className="text-xs text-muted-foreground">retry {job.attempt}</span>
        ) : null}
      </div>
      {running || job.status === "queued" ? (
        <Progress
          value={running && percent != null ? percent : null}
          className={cn(
            "h-1.5",
            !(running && percent != null) && "[&>[data-slot=progress-indicator]]:animate-pulse",
          )}
          aria-label={`${jobKindLabel(job.kind)} progress`}
        />
      ) : null}
      {!compact && detail.length ? (
        <p className="truncate text-xs text-muted-foreground" title={detail.join(" · ")}>
          {detail.join(" · ")}
        </p>
      ) : null}
    </div>
  );
}
