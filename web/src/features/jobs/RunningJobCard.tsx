import { Link } from "@tanstack/react-router";
import { Loader2, XCircle } from "lucide-react";

import { $api } from "@/api/client";
import type { JobRead } from "@/api/types";
import { FeedLink, type FeedSummary } from "@/components/common/FeedLink";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { JobProgress } from "./JobProgress";
import { useJobProgress } from "@/stores/progress";

/** One running job: live progress + phase, feed link, Episode title, speed/ETA, Cancel. */
export function RunningJobCard({
  job,
  feed,
  onCancel,
  cancelling = false,
}: {
  job: JobRead;
  feed?: FeedSummary;
  onCancel: (job: JobRead) => void;
  cancelling?: boolean;
}) {
  const live = useJobProgress(job.id);
  const itemId = job.item_id ?? live?.itemId ?? job.progress?.item_id ?? null;
  const item = $api.useQuery(
    "get",
    "/api/feeds/{feed_id}/items/{item_id}",
    { params: { path: { feed_id: job.feed_id ?? "", item_id: itemId ?? "" } } },
    { enabled: !!job.feed_id && !!itemId, staleTime: 60_000 },
  );
  const episodeTitle = item.data?.title ?? null;

  return (
    <Card data-testid="job-card" data-job-id={job.id}>
      <CardContent className="space-y-2 pt-4">
        <JobProgress job={job} />
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="flex min-w-0 flex-wrap items-center gap-x-1">
            {job.feed_id ? <FeedLink feedId={job.feed_id} feed={feed} /> : null}
            {itemId ? (
              <>
                <span aria-hidden>·</span>
                {job.feed_id && episodeTitle ? (
                  <Link
                    to={feed?.kind === "inbox" ? "/inboxes/$inboxId" : "/mirrors/$mirrorId"}
                    params={
                      feed?.kind === "inbox" ? { inboxId: job.feed_id } : { mirrorId: job.feed_id }
                    }
                    search={{ q: episodeTitle }}
                    className="truncate hover:underline"
                    title={episodeTitle}
                  >
                    {episodeTitle}
                  </Link>
                ) : (
                  <span className="truncate font-mono">{itemId}</span>
                )}
              </>
            ) : null}
            {job.request_id && job.feed_id ? (
              <>
                <span aria-hidden>·</span>
                <Link
                  to="/inboxes/$inboxId"
                  params={{ inboxId: job.feed_id }}
                  search={{ tab: "requests" }}
                  className="hover:underline"
                >
                  Request
                </Link>
              </>
            ) : null}
          </span>
          <Button
            size="xs"
            variant="outline"
            onClick={() => onCancel(job)}
            disabled={cancelling}
            aria-label={`Cancel ${job.kind} job`}
          >
            {cancelling ? <Loader2 className="animate-spin" /> : <XCircle />} Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
