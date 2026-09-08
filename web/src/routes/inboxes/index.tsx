import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { Inbox } from "lucide-react";
import { z } from "zod";

import { $api, asError } from "@/api/client";
import { isInbox, isMirror } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { PageHeader } from "@/components/common/PageHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { CaptureCard } from "@/features/inboxes/CaptureCard";
import { CaptureFeedCard } from "@/features/inboxes/CaptureFeedCard";
import { InboxCard } from "@/features/inboxes/InboxCard";
import { NewInboxDialog } from "@/features/inboxes/NewInboxDialog";

const defaults = { url: "" } as const;

export const Route = createFileRoute("/inboxes/")({
  validateSearch: z.object({ url: z.string().default(defaults.url) }),
  search: { middlewares: [stripSearchParams(defaults)] },
  component: InboxesPage,
});

function InboxesPage() {
  const { url } = Route.useSearch();
  const query = $api.useQuery("get", "/api/feeds");
  if (query.isError) throw asError(query.error);
  const feeds = query.data?.feeds ?? [];
  const inboxes = feeds.filter(isInbox);
  const captures = feeds.filter(isMirror).filter((feed) => feed.playlist_capture);
  return (
    <>
      <PageHeader
        title="Inboxes"
        description={
          url
            ? "Pick the Inbox that should receive the URL you pasted."
            : "Feeds without a Source, filled by the URLs you or your agents push into them."
        }
        actions={<NewInboxDialog />}
      />
      <div className="mb-6">
        <CaptureCard />
      </div>
      {query.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy>
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : inboxes.length === 0 ? (
        <EmptyState
          icon={<Inbox />}
          title="No Inboxes"
          description="The default Inbox appears once the api has started; create another one for a separate feed."
          action={<NewInboxDialog />}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {inboxes.map((inbox) => (
            <InboxCard key={inbox.id} inbox={inbox} url={url} />
          ))}
          {captures.map((feed) => (
            <CaptureFeedCard key={feed.id} feed={feed} />
          ))}
        </div>
      )}
    </>
  );
}
