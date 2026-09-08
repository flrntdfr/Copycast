import { ExternalLink, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import type { ItemRead } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";
import { useFetchMetadata } from "./mutations";
import { assetKindLabel } from "@/lib/labels";

/** RSS descriptions are HTML; show them as text. */
export function plainText(html: string | null | undefined): string {
  if (!html) return "";
  return html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>/gi, "\n\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function RowDetails({ item }: { item: ItemRead }) {
  const description = plainText(item.description);
  const fetchMetadata = useFetchMetadata(item.feed_id);
  const { mutate: fetchNow, status } = fetchMetadata;
  const canFetch = !item.description && !!item.item_url;
  // Expanding a row without a description asks the Source for it, once.
  const asked = useRef(false);
  useEffect(() => {
    if (!canFetch || asked.current) return;
    asked.current = true;
    fetchNow({ params: { path: { feed_id: item.feed_id, item_id: item.id } } });
  }, [canFetch, fetchNow, item.feed_id, item.id]);
  const fetching = canFetch && (status === "idle" || status === "pending");
  return (
    <div className="grid gap-4 text-sm md:grid-cols-[1fr_20rem]">
      <div className="min-w-0">
        {description ? (
          <p className="line-clamp-6 whitespace-pre-line text-muted-foreground">{description}</p>
        ) : fetching ? (
          <p className="flex items-center gap-2 text-muted-foreground" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Fetching the description from the Source…
          </p>
        ) : (
          <p className="text-muted-foreground italic">
            {canFetch && status === "error"
              ? "The Source did not answer; no description."
              : "No description."}
          </p>
        )}
        {item.item_url ? (
          <a
            href={item.item_url}
            target="_blank"
            rel="noreferrer"
            className="mt-2 inline-flex items-center gap-1 text-xs hover:underline"
          >
            Open at the Source <ExternalLink className="size-3" aria-hidden />
          </a>
        ) : null}
      </div>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Ordinal</dt>
        <dd className="tabular-nums">{item.ordinal}</dd>
        {item.source_number != null ? (
          <>
            <dt className="text-muted-foreground">Source number</dt>
            <dd className="tabular-nums">
              {item.source_season != null ? `S${item.source_season} · ` : ""}
              {item.source_number}
            </dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Added</dt>
        <dd>{formatDateTime(item.added_at)}</dd>
        {item.media ? (
          <>
            <dt className="text-muted-foreground">Media</dt>
            <dd>
              {item.media.mime} · {formatBytes(item.media.bytes)}
            </dd>
          </>
        ) : null}
        <dt className="text-muted-foreground">Downloads</dt>
        <dd>
          {formatNumber(item.download_count)}
          {item.last_downloaded_at ? ` · last ${formatDateTime(item.last_downloaded_at)}` : ""}
        </dd>
        {item.assets?.length ? (
          <>
            <dt className="text-muted-foreground">Assets</dt>
            <dd className="flex flex-wrap gap-1">
              {item.assets.map((asset) => {
                const text = [assetKindLabel(asset.kind), asset.language, asset.format]
                  .filter(Boolean)
                  .join(" ");
                return asset.url ? (
                  <a key={asset.id} href={asset.url} target="_blank" rel="noreferrer">
                    <Badge variant="outline">{text}</Badge>
                  </a>
                ) : (
                  <Badge
                    key={asset.id}
                    variant="outline"
                    className="text-muted-foreground"
                    title={asset.last_error ?? asset.state}
                  >
                    {text} ({asset.state})
                  </Badge>
                );
              })}
            </dd>
          </>
        ) : null}
        {item.request_ids?.length ? (
          <>
            <dt className="text-muted-foreground">Requests</dt>
            <dd className="font-mono">{item.request_ids.length}</dd>
          </>
        ) : null}
        {item.last_error ? (
          <>
            <dt className="text-muted-foreground">Last error</dt>
            <dd className="break-words text-destructive">{item.last_error}</dd>
          </>
        ) : null}
      </dl>
    </div>
  );
}
