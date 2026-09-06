import { History } from "lucide-react";

import { $api } from "@/api/client";
import type { JobRead } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { JobProgress } from "@/features/jobs/JobProgress";
import { elapsedMs, formatElapsed } from "@/lib/format";
import { jobStatusLabel, jobTriggerLabel } from "@/lib/labels";

/** What the refresh job writes into `JobRead.result`. */
export interface RefreshCounts {
  listed: number | null;
  new: number | null;
  delisted: number | null;
  wanted: number | null;
}

function numberOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function refreshCounts(job: Pick<JobRead, "result">): RefreshCounts | null {
  const result = job.result;
  if (!result) return null;
  const counts: RefreshCounts = {
    listed: numberOf(result.listed),
    new: numberOf(result.new),
    delisted: numberOf(result.delisted),
    wanted: numberOf(result.wanted),
  };
  return Object.values(counts).some((v) => v != null) ? counts : null;
}

export function describeCounts(counts: RefreshCounts | null): string {
  if (!counts) return "";
  const parts: string[] = [];
  if (counts.listed != null) parts.push(`${counts.listed.toLocaleString()} listed`);
  if (counts.new) parts.push(`${counts.new.toLocaleString()} new`);
  if (counts.delisted) parts.push(`${counts.delisted.toLocaleString()} delisted`);
  if (counts.wanted) parts.push(`${counts.wanted.toLocaleString()} queued`);
  return parts.join(" · ");
}

export function refreshDuration(
  job: Pick<JobRead, "started_at" | "finished_at" | "status">,
): string {
  if (!job.started_at) return "";
  if (job.status === "running") return formatElapsed(elapsedMs(job.started_at));
  return formatElapsed(elapsedMs(job.started_at, job.finished_at));
}

/** `list_jobs?feed_id&kind=refresh`: started, trigger, duration, status, counts, error. */
export function RefreshesTable({ feedId }: { feedId: string }) {
  const jobs = $api.useQuery(
    "get",
    "/api/jobs",
    { params: { query: { feed_id: feedId, kind: "refresh", limit: 50 } } },
    {
      refetchInterval: (query) =>
        query.state.data?.jobs.some((j) => j.status === "running") ? 5_000 : false,
    },
  );
  const rows = jobs.data?.jobs ?? [];

  if (jobs.isSuccess && rows.length === 0) {
    return (
      <EmptyState
        icon={<History />}
        title="No Refreshes yet"
        description="Each Refresh lists the Source and queues what the policy wants."
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Started</TableHead>
            <TableHead>Trigger</TableHead>
            <TableHead className="hidden md:table-cell">Duration</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="hidden md:table-cell">Counts</TableHead>
            <TableHead>Error</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.isPending ? <SkeletonRows columns={6} /> : null}
          {rows.map((job) => (
            <TableRow key={job.id} data-testid="refresh-row" data-job-status={job.status}>
              <TableCell className="whitespace-nowrap">
                <RelativeTime value={job.started_at ?? job.created_at} />
              </TableCell>
              <TableCell>{jobTriggerLabel(job.trigger)}</TableCell>
              <TableCell className="hidden tabular-nums md:table-cell">
                {refreshDuration(job)}
              </TableCell>
              <TableCell>
                {job.status === "running" ? (
                  <JobProgress job={job} compact />
                ) : (
                  jobStatusLabel(job.status)
                )}
                {job.status === "queued" && job.attempt > 0 ? (
                  <span className="block text-xs text-muted-foreground">retry {job.attempt}</span>
                ) : null}
              </TableCell>
              <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                {describeCounts(refreshCounts(job))}
              </TableCell>
              <TableCell
                className="max-w-xs truncate text-xs text-destructive"
                title={job.error ?? undefined}
              >
                {job.error ?? ""}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
