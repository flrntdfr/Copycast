import { useLocation } from "@tanstack/react-router";
import { ChevronRight, PaintBucket } from "lucide-react";
import { useState, type Ref } from "react";

import { $api, asError } from "@/api/client";
import { isBucket, isMirror, type FeedRead } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import type { MirrorsSort } from "@/features/mirrors/MirrorsTable";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { BucketCard } from "./BucketCard";
import { CapturePlaylistMenu } from "./CapturePlaylistMenu";
import { NewBucketDialog } from "./NewBucketDialog";

/** Where the box remembers whether it was left open ("1") or closed ("0"). */
export const BUCKETS_OPEN_KEY = "copycast.buckets.open";

function readRemembered(): boolean {
  try {
    return window.localStorage.getItem(BUCKETS_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function remember(open: boolean): void {
  try {
    window.localStorage.setItem(BUCKETS_OPEN_KEY, open ? "1" : "0");
  } catch {
    // Storage unavailable: the state lives for this page only.
  }
}

/**
 * One bucket per Feed, in the order of the Settings page's storage table: the Mirrors as the
 * table above lists them, then the Bucket Feeds, then the captured playlists.
 */
export function orderBuckets(feeds: readonly FeedRead[]): FeedRead[] {
  const mirrors = feeds.filter(isMirror);
  return [
    ...mirrors.filter((feed) => !feed.playlist_capture),
    ...feeds.filter(isBucket),
    ...mirrors.filter((feed) => feed.playlist_capture),
  ];
}

/**
 * The Buckets under the Feeds table, in a box collapsed by default: its header shows the count
 * and total size and opens it; inside, a card per Feed (every Feed keeps its files under its own
 * prefix of the Object store). Opened by a `#buckets` hash or a URL to send (the command
 * palette's "Send to Bucket…"), and remembered across visits.
 */
export function BucketsSection({
  sort = "title",
  order = "asc",
  url = "",
  ref,
}: {
  sort?: MirrorsSort;
  order?: "asc" | "desc";
  url?: string;
  ref?: Ref<HTMLElement>;
}) {
  const hash = useLocation({ select: (location) => location.hash });
  const query = $api.useQuery("get", "/api/feeds", { params: { query: { sort, order } } });
  // The page asks for the box open while a URL is to be sent or the hash points at it; that
  // request wins over a remembered state, and over an earlier click once it arrives anew.
  const wanted = url !== "" || hash === "buckets";
  // The request as a string, so a new URL or hash reopens the box even after a click closed it.
  const target = `${url}#${hash}`;
  const [remembered] = useState(readRemembered);
  const [choice, setChoice] = useState<{ open: boolean; target: string } | null>(null);
  const open = choice?.target !== target ? wanted || remembered : choice.open;
  const setOpen = (next: boolean) => {
    setChoice({ open: next, target });
    remember(next);
  };
  if (query.isError) throw asError(query.error);
  const all = orderBuckets(query.data?.feeds ?? []);
  // Only a Bucket Feed takes a URL: while one is to be sent, the Mirrors' buckets stay out.
  const buckets = url ? all.filter(isBucket) : all;
  const totalCount = all.length;
  const totalBytes = all.reduce((sum, feed) => sum + (feed.storage_bytes ?? 0), 0);

  return (
    <section id="buckets" ref={ref} aria-labelledby="buckets-heading" className="scroll-mt-20">
      <Collapsible open={open} onOpenChange={setOpen} className="rounded-lg border">
        <div className="flex flex-wrap items-center justify-between gap-2 p-2 pl-3">
          <h2 className="min-w-0 flex-1 text-lg font-semibold tracking-tight">
            <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-md py-1.5 text-left hover:text-foreground/80 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              <ChevronRight
                className={cn(
                  "size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
                  open && "rotate-90",
                )}
                aria-hidden
              />
              <span id="buckets-heading">Buckets</span>
              {query.isSuccess ? (
                <span className="truncate text-sm font-normal text-muted-foreground tabular-nums">
                  {count(totalCount, "bucket")} · {formatMegabytes(totalBytes)}
                </span>
              ) : null}
            </CollapsibleTrigger>
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <NewBucketDialog />
            <CapturePlaylistMenu />
          </div>
        </div>
        <CollapsibleContent className="border-t p-4">
          {url ? (
            <p className="mb-4 text-sm text-muted-foreground">
              Pick the Bucket that should receive the URL you pasted.
            </p>
          ) : null}
          {query.isPending ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy>
              {Array.from({ length: 3 }, (_, i) => (
                <Skeleton key={i} className="h-24" />
              ))}
            </div>
          ) : buckets.length === 0 ? (
            <EmptyState
              icon={<PaintBucket />}
              title="No Buckets"
              description="The default Bucket appears once the api has started; create another one for a separate feed."
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {buckets.map((feed) => (
                <BucketCard key={feed.id} feed={feed} url={url} />
              ))}
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}
