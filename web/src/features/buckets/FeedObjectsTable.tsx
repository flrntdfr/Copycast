import { Download, HardDrive, Lock, LockOpen } from "lucide-react";

import { $api } from "@/api/client";
import type { FeedObjectRead, FeedRead } from "@/api/types";
import { EmptyState } from "@/components/common/EmptyState";
import { RelativeTime } from "@/components/common/RelativeTime";
import { SkeletonRows } from "@/components/common/SkeletonRows";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatBytes } from "@/lib/format";
import { feedObjectKindLabel } from "@/lib/labels";
import { useSetLock } from "@/features/catalog/mutations";

const COLUMNS = 5;
const FEED_ITSELF = "Part of the feed itself: never purged";
const NOT_PURGED = "Not deleted by a Purge";

/** Why a file has no padlock: the feed's own files, or a file no Purge deletes. */
function noLockReason(object: FeedObjectRead | null): string {
  if (!object) return FEED_ITSELF;
  const feedOwn =
    object.item_id == null && ["descriptor", "source", "artwork"].includes(object.kind);
  return feedOwn ? FEED_ITSELF : NOT_PURGED;
}

/** `list_feed_objects` for one Feed: the header, the table and the Purge dialog share it. */
export function useFeedObjects(feedId: string) {
  return $api.useQuery("get", "/api/feeds/{feed_id}/objects", {
    params: { path: { feed_id: feedId } },
  });
}

/** The Feed URL without its Basic pair: the browser session already carries the operator's. */
export function bareFeedUrl(feedUrl: string): string {
  try {
    const url = new URL(feedUrl);
    url.username = "";
    url.password = "";
    return url.toString();
  } catch {
    return feedUrl;
  }
}

function DownloadLink({ href, name }: { href: string; name: string }) {
  return (
    <Button asChild variant="ghost" size="icon-sm">
      <a href={href} download={name} aria-label={`Download ${name}`}>
        <Download />
      </a>
    </Button>
  );
}

/**
 * The padlock of a file: the Lock of the Episode it belongs to, so every file of one Episode
 * shows the same state. The feed's own files have no Episode and are never purged.
 */
function LockToggle({ feedId, object }: { feedId: string; object: FeedObjectRead | null }) {
  const lock = useSetLock(feedId);
  const itemId = object?.item_id;
  const locked = object?.locked;

  if (!itemId || locked == null) {
    const reason = noLockReason(object);
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          {/* aria-disabled, not disabled: the padlock stays focusable so its tooltip shows. */}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={reason}
            aria-disabled="true"
            className="cursor-not-allowed opacity-50 hover:bg-transparent"
          >
            <Lock className="text-muted-foreground" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>{reason}</TooltipContent>
      </Tooltip>
    );
  }

  const tooltip = locked ? "Locked: click to unlock" : "Lock (never delete)";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Lock ${object.name}`}
          aria-pressed={locked}
          aria-disabled={lock.isPending || undefined}
          onClick={() => {
            if (lock.isPending) return;
            lock.mutate({
              params: { path: { feed_id: feedId, item_id: itemId } },
              body: { archive_flag: !locked },
            });
          }}
        >
          {locked ? <Lock className="text-primary" /> : <LockOpen />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

function ObjectRow({ feedId, object }: { feedId: string; object: FeedObjectRead }) {
  return (
    <TableRow data-testid="object-row">
      <TableCell>
        <div className="truncate font-mono text-xs" title={object.path}>
          {object.name}
        </div>
        {object.item_title ? (
          <div className="truncate text-xs text-muted-foreground" title={object.item_title}>
            {object.item_title}
          </div>
        ) : null}
      </TableCell>
      <TableCell className="hidden sm:table-cell">
        <Badge variant="outline">{feedObjectKindLabel(object.kind)}</Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums">{formatBytes(object.size_bytes)}</TableCell>
      <TableCell className="hidden md:table-cell">
        <RelativeTime value={object.last_modified} />
      </TableCell>
      <TableCell>
        <div className="flex items-center justify-end gap-0.5">
          <LockToggle feedId={feedId} object={object} />
          <DownloadLink href={object.url} name={object.name} />
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * Every file the Feed keeps under `feeds/{id}/` in the Object store, each downloadable and
 * lockable, with the published feed itself as the first row. The API's order stands.
 */
export function FeedObjectsTable({ feed }: { feed: FeedRead }) {
  const objects = useFeedObjects(feed.id);
  const rows = objects.data?.objects ?? [];
  const feedName = `${feed.id}.xml`;

  return (
    <section aria-labelledby="feed-objects-heading">
      <h2 id="feed-objects-heading" className="sr-only">
        Stored files
      </h2>
      <div className="overflow-x-auto rounded-lg border">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-auto">Name</TableHead>
              <TableHead className="hidden w-28 sm:table-cell">Kind</TableHead>
              <TableHead className="w-24 text-right">Size</TableHead>
              <TableHead className="hidden w-32 md:table-cell">Modified</TableHead>
              <TableHead className="w-20">
                <span className="sr-only">Lock and download</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow data-testid="object-row">
              <TableCell>
                <div className="truncate font-mono text-xs" title={feedName}>
                  {feedName}
                </div>
                <div className="truncate text-xs text-muted-foreground">The published feed</div>
              </TableCell>
              <TableCell className="hidden sm:table-cell">
                <Badge variant="outline">Feed</Badge>
              </TableCell>
              <TableCell className="text-right text-muted-foreground">—</TableCell>
              <TableCell className="hidden text-muted-foreground md:table-cell">—</TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-0.5">
                  <LockToggle feedId={feed.id} object={null} />
                  <DownloadLink href={bareFeedUrl(feed.feed_url)} name={feedName} />
                </div>
              </TableCell>
            </TableRow>
            {objects.isPending ? <SkeletonRows columns={COLUMNS} rows={3} /> : null}
            {objects.isError ? (
              <TableRow>
                <TableCell colSpan={COLUMNS} className="text-center text-destructive">
                  The stored files could not be listed.
                </TableCell>
              </TableRow>
            ) : null}
            {rows.map((object) => (
              <ObjectRow key={object.path} feedId={feed.id} object={object} />
            ))}
            {objects.isSuccess && rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={COLUMNS} className="py-6">
                  <EmptyState
                    icon={<HardDrive />}
                    title="Nothing stored yet"
                    description="Archived Episodes, their artwork and the Feed's own files appear here."
                  />
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
