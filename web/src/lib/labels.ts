/** Enum wording for the UI. Unknown values fall back to the raw string so a newer API never breaks a screen. */
import type {
  ArchiveState,
  AssetKind,
  BackfillMode,
  ErrorKind,
  HealthStatus,
  JobKind,
  JobStatus,
  JobTrigger,
  KeyScope,
  ProgressPhase,
  RequestStatus,
  RequestedVia,
  SourceKind,
} from "@/api/types";

/** The six derived Catalog states shown to people (AGENTS.md "Catalog"). */
export type CatalogState = "listed" | "delisted" | "available" | "queued" | "archiving" | "failed";

function label<T extends string>(
  table: Record<T, string>,
): (value: T | string | null | undefined) => string {
  return (value) => {
    if (value == null || value === "") return "";
    return (table as Record<string, string>)[value] ?? value;
  };
}

export const catalogStateLabel = label<CatalogState>({
  listed: "Listed",
  delisted: "Delisted",
  available: "Available",
  queued: "Queued",
  archiving: "Archiving",
  failed: "Failed",
});

export const archiveStateLabel = label<ArchiveState>({
  available: "Available",
  wanted: "Queued",
  archiving: "Archiving",
  archived: "Archived",
  failed: "Failed",
  deleted: "Deleted",
});

export const jobKindLabel = label<JobKind>({
  refresh: "Refresh",
  archive_item: "Archive",
  expand_request: "Expand Request",
  prune: "Prune",
  rebuild: "Rebuild",
});

export const jobStatusLabel = label<JobStatus>({
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
});

export const jobTriggerLabel = label<JobTrigger>({
  manual: "Manual",
  scheduled: "Scheduled",
  feed_fetch: "Feed fetch",
  request: "Request",
  policy: "Policy",
  ui: "UI",
  mcp: "MCP",
});

export const errorKindLabel = label<ErrorKind>({
  transient: "Transient",
  permanent: "Permanent",
  cancelled: "Cancelled",
  storage_full: "Storage full",
});

export const progressPhaseLabel = label<ProgressPhase>({
  listing: "Listing",
  downloading: "Downloading",
  postprocessing: "Processing",
  assets: "Assets",
});

export const backfillModeLabel = label<BackfillMode>({
  all: "Everything",
  latest: "Latest N",
  rolling: "Rolling N",
  automatic: "Automatic",
  selection: "Selection",
});

export const sourceKindLabel = label<SourceKind>({
  rss: "RSS",
  ytdlp: "Engine",
});

export const requestStatusLabel = label<RequestStatus>({
  queued: "Queued",
  expanded: "Expanded",
  failed: "Failed",
});

export const requestedViaLabel = label<RequestedVia>({
  ui: "UI",
  mcp: "MCP",
});

export const assetKindLabel = label<AssetKind>({
  artwork: "Artwork",
  chapters: "Chapters",
  transcript: "Transcript",
  attachment: "Attachment",
});

export const keyScopeLabel = label<KeyScope>({
  read: "Read",
  write: "Write",
  full: "Full",
});

/** What each API key scope lets an agent do over MCP. */
export const keyScopeHint: Record<KeyScope, string> = {
  read: "Read-only tools: list feeds and items, search, probe, read jobs.",
  write: "Everything except deletions: mirror, archive, push into Inboxes, pause, refresh.",
  full: "Everything, including delete_feed, delete_item and prune_inbox.",
};

export const healthStatusLabel = label<HealthStatus>({
  ok: "Healthy",
  warn: "Warning",
  error: "Failing",
  paused: "Paused",
  never: "Never refreshed",
});

/** Wording for the derived Catalog state of an item, reused by StateBadge tooltips. */
export const catalogStateHint: Record<CatalogState, string> = {
  listed: "Archived and listed by the Source",
  delisted: "No longer listed by the Source; kept in the Mirror Feed",
  available: "Listed by the Source, not archived",
  queued: "Waiting for the worker",
  archiving: "The worker is downloading it",
  failed: "The last attempt failed",
};

export const TOMBSTONE_HINT = "Deleted by you; won't re-archive automatically";

/** Pluralised domain nouns, e.g. `count(3, "Episode")` -> "3 Episodes". */
export function count(n: number, singular: string, plural = `${singular}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : plural}`;
}
