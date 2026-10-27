import { Link } from "@tanstack/react-router";

import { $api } from "@/api/client";
import type { InboxRead } from "@/api/types";
import { RelativeTime } from "@/components/common/RelativeTime";
import { StorageDot } from "@/components/common/StorageDot";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { count } from "@/lib/labels";

/** One Inbox on the list, kept minimal: storage dot, name, last update, Episode count. */
export function InboxCard({ inbox, url = "" }: { inbox: InboxRead; url?: string }) {
  const last = $api.useQuery(
    "get",
    "/api/inboxes/{inbox_id}/requests",
    { params: { path: { inbox_id: inbox.id }, query: { limit: 1 } } },
    { enabled: inbox.request_count > 0 },
  );
  const lastRequest = last.data?.requests[0] ?? null;
  return (
    <Link
      to="/inboxes/$inboxId"
      params={{ inboxId: inbox.id }}
      search={url ? { tab: "requests", url } : {}}
      className="rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      data-testid="inbox-card"
    >
      <Card className="h-full gap-2 py-4 transition-colors hover:bg-accent/40">
        <CardHeader className="px-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <StorageDot reachable={null} />
            <span className="truncate">{inbox.name}</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between gap-2 px-4 text-sm text-muted-foreground">
          <span className="truncate">
            {lastRequest ? (
              <>
                Refreshed <RelativeTime value={lastRequest.created_at} />
              </>
            ) : (
              "Never refreshed"
            )}
          </span>
          <span className="shrink-0 tabular-nums">{count(inbox.episode_count, "Episode")}</span>
        </CardContent>
      </Card>
    </Link>
  );
}
