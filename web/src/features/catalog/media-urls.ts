/**
 * Where the browser fetches an Episode's media and assets: the session's own play routes
 * (`GET /api/feeds/{id}/items/{item}/media`, ADR U10), never the feed's `/feeds/…` URLs, so the
 * web UI never needs a feed secret. The feed URLs remain a fallback for an api that does not
 * give play URLs yet.
 */
import type { AssetRead, ItemRead } from "@/api/types";

/** What the player streams: the play URL (absolute, stable across reloads). */
export function playUrlOf(item: Pick<ItemRead, "media">): string | null {
  return item.media?.play_url ?? item.media?.url ?? null;
}

/** The Download link: the play URL with `?download=1`, which names the file after the Episode. */
export function downloadUrlOf(item: Pick<ItemRead, "media">): string | null {
  const playUrl = item.media?.play_url;
  if (!playUrl) return item.media?.url ?? null;
  try {
    const url = new URL(playUrl, window.location.origin);
    url.searchParams.set("download", "1");
    return url.toString();
  } catch {
    return `${playUrl}${playUrl.includes("?") ? "&" : "?"}download=1`;
  }
}

/** An item asset (chapters, transcript, attachment) for the browser. */
export function assetUrlOf(asset: Pick<AssetRead, "play_url" | "url">): string | null {
  return asset.play_url ?? asset.url ?? null;
}

/** The chapters asset the player marks on its seek bar, if archived. */
export function chaptersUrlOf(item: Pick<ItemRead, "assets">): string | null {
  const chapters = item.assets?.find((asset) => asset.kind === "chapters" && assetUrlOf(asset));
  return chapters ? assetUrlOf(chapters) : null;
}
