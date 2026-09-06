import { Link } from "@tanstack/react-router";
import { CheckCircle2 } from "lucide-react";

import { $api } from "@/api/client";
import type { MirrorRead } from "@/api/types";
import { FeedUrlField } from "@/components/common/FeedUrlField";
import { Button } from "@/components/ui/button";
import { JobProgress } from "@/features/jobs/JobProgress";
import { count } from "@/lib/labels";

export function CreatedStep({ mirror }: { mirror: MirrorRead }) {
  const jobs = $api.useQuery(
    "get",
    "/api/jobs",
    { params: { query: { feed_id: mirror.id, kind: "refresh", limit: 1 } } },
    {
      refetchInterval: (query) =>
        query.state.data?.jobs.some((j) => j.status === "running" || j.status === "queued")
          ? 5_000
          : false,
    },
  );
  const firstRefresh = jobs.data?.jobs[0] ?? null;
  const selection = mirror.backfill.mode === "selection";

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm" role="status">
        <CheckCircle2 className="size-5 text-success" aria-hidden />
        <span>
          <span className="font-medium">{mirror.title}</span> is now a Mirror.
        </span>
      </div>
      <FeedUrlField url={mirror.feed_url} label="Mirror Feed URL" />
      <p className="text-sm text-muted-foreground">
        In your podcast app choose “Add by URL” (sometimes “Add a show by URL” or “Subscribe to
        feed”) and paste this address. Episodes appear there as they are archived.
      </p>
      <div className="rounded-lg border bg-card p-3">
        {selection ? (
          <p className="text-sm">
            {count(mirror.selection?.count ?? 0, "Episode")} queued for archiving
            {mirror.follow
              ? "; new items will follow."
              : "; nothing else is archived unless you ask."}
          </p>
        ) : firstRefresh ? (
          <JobProgress job={firstRefresh} />
        ) : (
          <p className="text-sm text-muted-foreground">The first Refresh is being queued…</p>
        )}
      </div>
      <div className="flex gap-2">
        <Button asChild>
          <Link to="/mirrors/$mirrorId" params={{ mirrorId: mirror.id }}>
            Open Mirror
          </Link>
        </Button>
        <Button variant="outline" asChild>
          <Link to="/mirrors/new" search={{ step: "probe" }}>
            Add another
          </Link>
        </Button>
      </div>
    </div>
  );
}
