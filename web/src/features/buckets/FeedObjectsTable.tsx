import { Download, HardDrive } from "lucide-react";

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
import { formatBytes, formatMegabytes } from "@/lib/format";
import { count, feedObjectKindLabel } from "@/lib/labels";

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

function ObjectRow({ object }: { object: FeedObjectRead }) {
  return (
    <TableRow data-testid="object-row">
      <TableCell className="max-w-0">
        <div className="truncate font-mono text-xs" title={object.path}>
          {object.name}
        </div>
        {object.item_title ? (
          <div className="truncate text-xs text-muted-foreground" title={object.item_title}>
            {object.item_title}
          </div>
        ) : null}
      </TableCell>
      <TableCell>
        <Badge variant="outline">{feedObjectKindLabel(object.kind)}</Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums">{formatBytes(object.size_bytes)}</TableCell>
      <TableCell className="hidden md:table-cell">
        <RelativeTime value={object.last_modified} />
      </TableCell>
      <TableCell className="text-right">
        <DownloadLink href={object.url} name={object.name} />
      </TableCell>
    </TableRow>
  );
}

/**
 * Every object the Feed keeps under `feeds/{id}/` in the Object store, each downloadable, with
 * the published feed itself as the first row. No filter, no selection: the API's order stands.
 */
export function FeedObjectsTable({ feed }: { feed: FeedRead }) {
  const objects = $api.useQuery("get", "/api/feeds/{feed_id}/objects", {
    params: { path: { feed_id: feed.id } },
  });
  const rows = objects.data?.objects ?? [];
  const feedName = `${feed.id}.xml`;

  return (
    <section aria-labelledby="feed-objects-heading" className="space-y-2">
      <h2 id="feed-objects-heading" className="sr-only">
        Stored files
      </h2>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead className="text-right">Size</TableHead>
              <TableHead className="hidden md:table-cell">Modified</TableHead>
              <TableHead className="text-right">
                <span className="sr-only">Download</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow data-testid="object-row">
              <TableCell className="max-w-0">
                <div className="truncate font-mono text-xs">{feedName}</div>
                <div className="truncate text-xs text-muted-foreground">The published feed</div>
              </TableCell>
              <TableCell>
                <Badge variant="outline">Feed</Badge>
              </TableCell>
              <TableCell className="text-right text-muted-foreground">—</TableCell>
              <TableCell className="hidden text-muted-foreground md:table-cell">—</TableCell>
              <TableCell className="text-right">
                <DownloadLink href={bareFeedUrl(feed.feed_url)} name={feedName} />
              </TableCell>
            </TableRow>
            {objects.isPending ? <SkeletonRows columns={5} rows={3} /> : null}
            {objects.isError ? (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-destructive">
                  The stored files could not be listed.
                </TableCell>
              </TableRow>
            ) : null}
            {rows.map((object) => (
              <ObjectRow key={object.path} object={object} />
            ))}
            {objects.isSuccess && rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="py-6">
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
      {objects.data ? (
        <p className="text-xs text-muted-foreground tabular-nums" data-testid="objects-summary">
          {count(objects.data.count, "file")} · {formatMegabytes(objects.data.total_bytes)}
        </p>
      ) : null}
    </section>
  );
}
