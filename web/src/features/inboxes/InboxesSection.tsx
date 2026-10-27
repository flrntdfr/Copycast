import { Inbox } from "lucide-react";
import type { Ref } from "react";

import { $api, asError } from "@/api/client";
import { isInbox, isMirror } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { SectionHeader } from "@/components/common/PageHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { CaptureFeedCard } from "./CaptureFeedCard";
import { CapturePlaylistMenu } from "./CapturePlaylistMenu";
import { InboxCard } from "./InboxCard";
import { NewInboxDialog } from "./NewInboxDialog";

/**
 * The Inboxes under the Mirrors: a card per Inbox, then the captured YouTube playlists, with
 * New Inbox and the playlist dropdown. With `url`, each card opens its Inbox ready to add it.
 */
export function InboxesSection({ url = "", ref }: { url?: string; ref?: Ref<HTMLElement> }) {
  const query = $api.useQuery("get", "/api/feeds");
  if (query.isError) throw asError(query.error);
  const feeds = query.data?.feeds ?? [];
  const inboxes = feeds.filter(isInbox);
  const captures = feeds.filter(isMirror).filter((feed) => feed.playlist_capture);
  return (
    <section id="inboxes" ref={ref} aria-labelledby="inboxes-heading" className="scroll-mt-20">
      <SectionHeader
        id="inboxes-heading"
        title="Inboxes"
        description={url ? "Pick the Inbox that should receive the URL you pasted." : undefined}
        actions={
          <>
            <NewInboxDialog />
            <CapturePlaylistMenu />
          </>
        }
      />
      {query.isPending ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy>
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : inboxes.length === 0 && captures.length === 0 ? (
        <EmptyState
          icon={<Inbox />}
          title="No Inboxes"
          description="The default Inbox appears once the api has started; create another one for a separate feed."
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
    </section>
  );
}
