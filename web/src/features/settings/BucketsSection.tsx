import { Inbox, ListVideo, Radio, Trash2 } from "lucide-react";
import { Fragment, useState } from "react";

import { $api, asError } from "@/api/client";
import { isInbox, isMirror, type FeedRead } from "@/api/types";
import { SectionHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatMegabytes } from "@/lib/format";
import { HelpTip } from "./AuthenticationsSection";
import { BucketDeleteDialog } from "./BucketDeleteDialog";

/**
 * Every Feed's storage: the Mirrors in the Feeds page's order, then the Inboxes and the
 * captured YouTube playlists, each with its size and a trash button.
 */
export function BucketsSection() {
  // The same queries as the Feeds page, so the order is the one shown there.
  const mirrors = $api.useQuery("get", "/api/feeds", {
    params: { query: { kind: "mirror", sort: "title", order: "asc" } },
  });
  const feeds = $api.useQuery("get", "/api/feeds");
  if (mirrors.isError) throw asError(mirrors.error);
  if (feeds.isError) throw asError(feeds.error);
  const [deleting, setDeleting] = useState<FeedRead | null>(null);

  const mirrorRows = (mirrors.data?.feeds ?? [])
    .filter(isMirror)
    .filter((feed) => !feed.playlist_capture);
  const all = feeds.data?.feeds ?? [];
  const individual: FeedRead[] = [
    ...all.filter(isInbox),
    ...all.filter(isMirror).filter((feed) => feed.playlist_capture),
  ];
  const groups = [
    { id: "mirrors", label: "Mirrors", rows: mirrorRows },
    { id: "individual", label: "Inboxes and playlists", rows: individual },
  ];
  const total = [...mirrorRows, ...individual].reduce(
    (sum, feed) => sum + (feed.storage_bytes ?? 0),
    0,
  );

  return (
    <section aria-labelledby="buckets-heading">
      <SectionHeader
        id="buckets-heading"
        title={
          <span className="inline-flex items-center gap-1.5">
            Buckets
            <HelpTip label="About buckets">
              Each Feed keeps its media in its own bucket. The trash button deletes the Feed and its
              bucket; a bucket holding Episodes flagged Archive warns first and can delete only the
              rest.
            </HelpTip>
          </span>
        }
        actions={
          <span className="text-xs text-muted-foreground tabular-nums">
            {mirrors.isSuccess && feeds.isSuccess ? `${formatMegabytes(total)} in all` : null}
          </span>
        }
      />
      {mirrors.isPending || feeds.isPending ? (
        <Skeleton className="h-80" />
      ) : (
        <div className="rounded-lg border">
          <Table aria-label="Buckets">
            <TableHeader>
              <TableRow>
                <TableHead>Bucket</TableHead>
                <TableHead className="text-right">Size</TableHead>
                <TableHead className="w-10">
                  <span className="sr-only">Delete</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => (
                <Fragment key={group.id}>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell
                      colSpan={3}
                      className="py-1.5 text-xs font-medium text-muted-foreground"
                    >
                      {group.label}
                    </TableCell>
                  </TableRow>
                  {group.rows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3} className="text-muted-foreground">
                        None
                      </TableCell>
                    </TableRow>
                  ) : (
                    group.rows.map((feed) => (
                      <BucketRow key={feed.id} feed={feed} onDelete={() => setDeleting(feed)} />
                    ))
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <BucketDeleteDialog feed={deleting} onClose={() => setDeleting(null)} />
    </section>
  );
}

function BucketRow({ feed, onDelete }: { feed: FeedRead; onDelete: () => void }) {
  const Icon = isInbox(feed) ? Inbox : feed.playlist_capture ? ListVideo : Radio;
  return (
    <TableRow data-testid={`bucket-${feed.id}`}>
      <TableCell className="w-full max-w-0">
        <span className="flex min-w-0 items-center gap-2">
          <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="truncate">{feed.title}</span>
        </span>
      </TableCell>
      <TableCell className="text-right whitespace-nowrap tabular-nums">
        {formatMegabytes(feed.storage_bytes)}
      </TableCell>
      <TableCell>
        <Button
          size="icon"
          variant="ghost"
          className="size-8 text-muted-foreground hover:text-destructive"
          aria-label={`Delete ${feed.title}`}
          onClick={onDelete}
        >
          <Trash2 />
        </Button>
      </TableCell>
    </TableRow>
  );
}
