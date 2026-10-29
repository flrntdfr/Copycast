/** SyncBadge colour and time for any Feed: when it last synced and whether that is healthy. */
import { isMirror, type FeedRead } from "@/api/types";

export type SyncTone = "success" | "warning";

export interface SyncView {
  /** Green when the Feed is healthy and has synced, orange otherwise. */
  tone: SyncTone;
  /** When it last synced: a Mirror's last successful Refresh, a Bucket Feed's last Request. */
  at: string | null;
}

/**
 * A Mirror is green when its health is `ok`, it is not Paused and a Refresh has succeeded. A
 * Bucket Feed never Refreshes: it is green once it holds a Request or an Episode, orange while
 * empty; `lastRequestAt` is the time of its newest Request when the caller has fetched it.
 */
export function syncView(feed: FeedRead, lastRequestAt: string | null = null): SyncView {
  if (isMirror(feed)) {
    const at = feed.last_refresh_success_at ?? null;
    const healthy = feed.health.status === "ok" && !feed.paused && at !== null;
    return { tone: healthy ? "success" : "warning", at };
  }
  const synced = feed.request_count > 0 || feed.episode_count > 0;
  return { tone: synced ? "success" : "warning", at: lastRequestAt };
}
