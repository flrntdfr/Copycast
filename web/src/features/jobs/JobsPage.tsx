import { Activity } from "lucide-react";
import { toast } from "sonner";

import { $api } from "@/api/client";
import type { JobRead } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { FeedLink, useFeedIndex } from "@/components/common/FeedLink";
import { PageHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { JobsTable } from "./JobsTable";
import { RunningJobCard } from "./RunningJobCard";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { jobKindLabel, jobTriggerLabel } from "@/lib/labels";

/** Running Cards, the queue and the recent history; `job` events keep it current. */
export function JobsPage() {
  const invalidate = useInvalidateFeed();
  const feeds = useFeedIndex();
  const active = $api.useQuery(
    "get",
    "/api/jobs",
    { params: { query: { status: ["running", "queued"], limit: 200 } } },
    { refetchInterval: 15_000 },
  );
  const recent = $api.useQuery("get", "/api/jobs", {
    params: { query: { status: ["succeeded", "failed", "cancelled"], limit: 50 } },
  });
  const cancel = $api.useMutation("post", "/api/jobs/{job_id}/cancel", {
    onSuccess: (job) => {
      toast.success(job.status === "cancelled" ? "Job cancelled" : "Cancellation requested", {
        description:
          job.status === "cancelled" ? undefined : "The worker stops within a few seconds.",
      });
      invalidate(job.feed_id ?? undefined);
    },
  });
  const cancelJob = (job: JobRead) => cancel.mutate({ params: { path: { job_id: job.id } } });
  const running = (active.data?.jobs ?? []).filter((job) => job.status === "running");
  const queued = (active.data?.jobs ?? []).filter((job) => job.status === "queued");
  const idle = active.isSuccess && running.length === 0 && queued.length === 0;

  return (
    <>
      <PageHeader
        title="Jobs"
        description="What the worker is doing right now, what waits, and what it did recently."
      />
      {active.isError ? (
        <p role="alert" className="mb-6 text-sm text-destructive">
          The queue could not be loaded; it is retried automatically.
        </p>
      ) : null}
      {idle ? (
        <div className="mb-8">
          <EmptyState
            icon={<Activity />}
            title="The worker is idle"
            description="Refreshes and downloads show up here with live progress as they run."
          />
        </div>
      ) : null}
      {!idle ? (
        <>
          <section aria-labelledby="running-heading" className="mb-8">
            <h2 id="running-heading" className="mb-3 text-sm font-medium">
              Running ({running.length})
            </h2>
            {active.isPending ? (
              <div className="grid gap-3 md:grid-cols-2">
                <Skeleton className="h-24" />
                <Skeleton className="h-24" />
              </div>
            ) : running.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing is running.</p>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {running.map((job) => (
                  <RunningJobCard
                    key={job.id}
                    job={job}
                    feed={job.feed_id ? feeds.get(job.feed_id) : undefined}
                    onCancel={cancelJob}
                    cancelling={cancel.isPending && cancel.variables?.params.path.job_id === job.id}
                  />
                ))}
              </div>
            )}
          </section>
          <section aria-labelledby="queued-heading" className="mb-8">
            <h2 id="queued-heading" className="mb-3 text-sm font-medium">
              Queued ({queued.length})
            </h2>
            {queued.length === 0 ? (
              <p className="text-sm text-muted-foreground">The queue is empty.</p>
            ) : (
              <ul className="divide-y rounded-lg border text-sm">
                {queued.map((job) => (
                  <li
                    key={job.id}
                    className="flex items-center justify-between gap-2 px-3 py-2"
                    data-testid="queued-job"
                  >
                    <span className="flex min-w-0 flex-wrap items-center gap-x-1">
                      <span className="font-medium">{jobKindLabel(job.kind)}</span>
                      <span className="text-muted-foreground">
                        · {jobTriggerLabel(job.trigger)}
                      </span>
                      {job.feed_id ? (
                        <>
                          <span className="text-muted-foreground" aria-hidden>
                            ·
                          </span>
                          <FeedLink feedId={job.feed_id} feed={feeds.get(job.feed_id)} />
                        </>
                      ) : null}
                      {job.attempt > 0 ? (
                        <span className="text-xs text-muted-foreground">
                          · retry {job.attempt}
                          {job.error ? `: ${job.error}` : ""}
                        </span>
                      ) : null}
                    </span>
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() => cancelJob(job)}
                      disabled={cancel.isPending}
                      aria-label={`Cancel queued ${job.kind} job`}
                    >
                      Cancel
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
      <section aria-labelledby="recent-heading">
        <h2 id="recent-heading" className="mb-3 text-sm font-medium">
          Recent
        </h2>
        <JobsTable
          jobs={recent.data?.jobs ?? []}
          feeds={feeds}
          loading={recent.isPending}
          error={recent.isError}
        />
      </section>
    </>
  );
}
