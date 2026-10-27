import { PaintBucket } from "lucide-react";
import type { Ref } from "react";

import { $api, asError } from "@/api/client";
import { isBucket, isMirror } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { SectionHeader } from "@/components/common/PageHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { CaptureFeedCard } from "./CaptureFeedCard";
import { CapturePlaylistMenu } from "./CapturePlaylistMenu";
import { BucketCard } from "./BucketCard";
import { NewBucketDialog } from "./NewBucketDialog";

/**
 * The Buckets under the Mirrors: a card per Bucket, then the captured YouTube playlists, with
 * New Bucket and the playlist dropdown. With `url`, each card opens its Bucket ready to add it.
 */
export function BucketsSection({ url = "", ref }: { url?: string; ref?: Ref<HTMLElement> }) {
  const query = $api.useQuery("get", "/api/feeds");
  if (query.isError) throw asError(query.error);
  const feeds = query.data?.feeds ?? [];
  const buckets = feeds.filter(isBucket);
  const captures = feeds.filter(isMirror).filter((feed) => feed.playlist_capture);
  return (
    <section id="buckets" ref={ref} aria-labelledby="buckets-heading" className="scroll-mt-20">
      <SectionHeader
        id="buckets-heading"
        title="Buckets"
        description={url ? "Pick the Bucket that should receive the URL you pasted." : undefined}
        actions={
          <>
            <NewBucketDialog />
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
      ) : buckets.length === 0 && captures.length === 0 ? (
        <EmptyState
          icon={<PaintBucket />}
          title="No Buckets"
          description="The default Bucket appears once the api has started; create another one for a separate feed."
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {buckets.map((bucket) => (
            <BucketCard key={bucket.id} bucket={bucket} url={url} />
          ))}
          {captures.map((feed) => (
            <CaptureFeedCard key={feed.id} feed={feed} />
          ))}
        </div>
      )}
    </section>
  );
}
