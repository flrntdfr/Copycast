import { Link } from "@tanstack/react-router";
import { Inbox, MoreHorizontal, Scissors, Settings2, Trash2 } from "lucide-react";
import { useState } from "react";

import type { InboxRead } from "@/api/types";
import { FeedCredentials } from "@/components/common/FeedCredentials";
import { FeedUrlField } from "@/components/common/FeedUrlField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AddRequestForm } from "./AddRequestForm";
import { PruneDialog } from "./PruneDialog";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { formatBytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** "Autoprune 30 days after first download" / "No autoprune". */
export function describeAutoprune(inbox: Pick<InboxRead, "autoprune_days">): string {
  return inbox.autoprune_days
    ? `Autoprune ${count(inbox.autoprune_days, "day")} after the first download`
    : "No autoprune";
}

/** Name, counts and size, the Inbox Feed URL, the Add URL form and the actions menu. */
export function InboxHeader({ inbox, initialUrl = "" }: { inbox: InboxRead; initialUrl?: string }) {
  const [pruning, setPruning] = useState(false);
  const [deleting, setDeleting] = useState(false);
  return (
    <header className="mb-6 space-y-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div
          className="hidden size-16 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground sm:flex"
          aria-hidden
        >
          <Inbox className="size-7" />
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{inbox.name}</h1>
            <Badge variant="outline">Inbox</Badge>
          </div>
          <p className="text-sm text-muted-foreground tabular-nums">
            {count(inbox.episode_count, "Episode")} · {count(inbox.request_count, "Request")} ·{" "}
            {formatBytes(inbox.storage_bytes)} · {describeAutoprune(inbox)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="outline" size="sm" onClick={() => setPruning(true)}>
            <Scissors /> Prune now…
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="More actions">
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link
                  to="/inboxes/$inboxId"
                  params={{ inboxId: inbox.id }}
                  search={{ tab: "settings" }}
                >
                  <Settings2 /> Settings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(true)}>
                <Trash2 /> Delete…
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          <FeedUrlField url={inbox.feed_url} label="Inbox Feed URL" />
          <FeedCredentials feed={inbox} />
        </div>
        <AddRequestForm inboxId={inbox.id} initialUrl={initialUrl} />
      </div>
      <PruneDialog inboxId={inbox.id} open={pruning} onOpenChange={setPruning} />
      <DeleteFeedDialog feed={inbox} open={deleting} onOpenChange={setDeleting} />
    </header>
  );
}
