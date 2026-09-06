import type { FeedSummary } from "@/components/common/FeedLink";
import { FeedLink } from "@/components/common/FeedLink";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import type { JobRead } from "@/api/types";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { elapsedMs, formatElapsed } from "@/lib/format";
import { errorKindLabel, jobKindLabel, jobStatusLabel, jobTriggerLabel } from "@/lib/labels";

/** Recently finished jobs: kind, trigger, status, feed, duration, finished, error. */
export function JobsTable({
  jobs,
  feeds,
  loading = false,
  error = false,
}: {
  jobs: JobRead[];
  feeds: Map<string, FeedSummary>;
  loading?: boolean;
  error?: boolean;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Kind</TableHead>
            <TableHead className="hidden sm:table-cell">Trigger</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Feed</TableHead>
            <TableHead className="hidden md:table-cell">Duration</TableHead>
            <TableHead>Finished</TableHead>
            <TableHead className="hidden md:table-cell">Error</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {loading ? <SkeletonRows columns={7} rows={4} /> : null}
          {error ? (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-destructive">
                Recent jobs could not be loaded.
              </TableCell>
            </TableRow>
          ) : null}
          {!loading && !error && jobs.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">
                No job has finished yet.
              </TableCell>
            </TableRow>
          ) : null}
          {jobs.map((job) => (
            <TableRow key={job.id} data-testid="job-row" data-job-status={job.status}>
              <TableCell className="whitespace-nowrap">{jobKindLabel(job.kind)}</TableCell>
              <TableCell className="hidden sm:table-cell">{jobTriggerLabel(job.trigger)}</TableCell>
              <TableCell className="whitespace-nowrap">
                {jobStatusLabel(job.status)}
                {job.status === "failed" && job.error_kind ? (
                  <span className="block text-xs text-muted-foreground">
                    {errorKindLabel(job.error_kind)}
                    {job.attempt > 1 ? ` · attempt ${job.attempt}` : ""}
                  </span>
                ) : null}
              </TableCell>
              <TableCell className="max-w-[12rem] truncate">
                {job.feed_id ? <FeedLink feedId={job.feed_id} feed={feeds.get(job.feed_id)} /> : ""}
              </TableCell>
              <TableCell className="hidden tabular-nums md:table-cell">
                {job.started_at ? formatElapsed(elapsedMs(job.started_at, job.finished_at)) : ""}
              </TableCell>
              <TableCell className="whitespace-nowrap">
                <RelativeTime value={job.finished_at} />
              </TableCell>
              <TableCell
                className="hidden max-w-xs truncate text-xs text-destructive md:table-cell"
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
