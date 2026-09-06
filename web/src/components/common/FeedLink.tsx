import { Link } from "@tanstack/react-router";

import { $api } from "@/api/client";
import type { FeedKind, FeedRead } from "@/api/types";
import { cn } from "@/lib/utils";

export interface FeedSummary {
  id: string;
  kind: FeedKind;
  title: string;
}

/** Feed titles and kinds by id from `list_feeds`, for links in Jobs and Requests. */
export function useFeedIndex(): Map<string, FeedSummary> {
  const { data } = $api.useQuery("get", "/api/feeds");
  const index = new Map<string, FeedSummary>();
  for (const feed of data?.feeds ?? []) {
    index.set(feed.id, { id: feed.id, kind: feed.kind, title: feed.title });
  }
  return index;
}

/** Link to a Mirror or an Inbox page; falls back to the raw id until the index has it. */
export function FeedLink({
  feedId,
  feed,
  className,
}: {
  feedId: string;
  feed?: Pick<FeedRead, "id" | "kind" | "title"> | FeedSummary;
  className?: string;
}) {
  const classes = cn("truncate hover:underline", className);
  if (!feed) {
    return (
      <span className={cn("font-mono text-xs text-muted-foreground", className)}>{feedId}</span>
    );
  }
  return feed.kind === "mirror" ? (
    <Link to="/mirrors/$mirrorId" params={{ mirrorId: feed.id }} className={classes}>
      {feed.title}
    </Link>
  ) : (
    <Link to="/inboxes/$inboxId" params={{ inboxId: feed.id }} className={classes}>
      {feed.title}
    </Link>
  );
}
