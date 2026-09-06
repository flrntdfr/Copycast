import { $api } from "@/api/client";
import type { JobKind, JobRead } from "@/api/types";

/** Running jobs, refreshed by `job` events (and a slow poll as a safety net). */
export function useRunningJobs(kind?: JobKind): JobRead[] {
  const { data } = $api.useQuery(
    "get",
    "/api/jobs",
    { params: { query: { status: ["running"], limit: 500, ...(kind ? { kind } : {}) } } },
    { refetchInterval: 60_000 },
  );
  return data?.jobs ?? [];
}

/** Feed ids with a running Refresh, for HealthDot blinking. */
export function useRunningRefreshFeedIds(): Set<string> {
  const jobs = useRunningJobs("refresh");
  return new Set(jobs.flatMap((job) => (job.feed_id ? [job.feed_id] : [])));
}
