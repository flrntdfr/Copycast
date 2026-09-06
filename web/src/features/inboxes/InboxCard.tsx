import { Link } from "@tanstack/react-router";
import { Inbox } from "lucide-react";

import { $api } from "@/api/client";
import type { InboxRead } from "@/api/types";
import { RelativeTime } from "@/components/common/RelativeTime";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { describeAutoprune } from "./InboxHeader";
import { formatBytes } from "@/lib/format";
import { count, requestStatusLabel } from "@/lib/labels";

/** One Inbox on the list: name, Episodes, size, the last Request and the autoprune summary. */
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
      <Card className="h-full transition-colors hover:bg-accent/40">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Inbox className="size-4 text-muted-foreground" aria-hidden /> {inbox.name}
          </CardTitle>
          <CardDescription className="tabular-nums">
            {count(inbox.episode_count, "Episode")} · {formatBytes(inbox.storage_bytes)} ·{" "}
            {count(inbox.request_count, "Request")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm text-muted-foreground">
          <p className="truncate">
            {inbox.request_count === 0 ? (
              "No Requests yet"
            ) : lastRequest ? (
              <>
                Last Request <RelativeTime value={lastRequest.created_at} /> ·{" "}
                {requestStatusLabel(lastRequest.status)}
              </>
            ) : (
              "Last Request …"
            )}
          </p>
          <p>{describeAutoprune(inbox)}</p>
        </CardContent>
      </Card>
    </Link>
  );
}
