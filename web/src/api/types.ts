/** Named aliases over the generated OpenAPI schema; the only place the UI spells the paths. */
import type { components, operations, paths } from "./schema";

export type Schemas = components["schemas"];
export type Paths = paths;
export type Operations = operations;

export type FeedRead = Schemas["MirrorRead"] | Schemas["InboxRead"];
export type MirrorRead = Schemas["MirrorRead"];
export type InboxRead = Schemas["InboxRead"];
export type FeedList = Schemas["FeedList"];
export type ItemRead = Schemas["ItemRead"];
export type ItemPage = Schemas["ItemPage"];
export type ItemMedia = Schemas["ItemMedia"];
export type AssetRead = Schemas["AssetRead"];
export type JobRead = Schemas["JobRead"];
export type JobPage = Schemas["JobPage"];
export type JobProgress = Schemas["JobProgress"];
export type ProbeRequest = Schemas["ProbeRequest"];
export type ProbeResult = Schemas["ProbeResult"];
export type ProbeCandidate = Schemas["ProbeCandidate"];
export type PodcastSearchPage = Schemas["PodcastSearchPage"];
export type PodcastSearchResult = Schemas["PodcastSearchResult"];
export type MirrorCreate = Schemas["MirrorCreate"];
export type MirrorUpdate = Schemas["MirrorUpdate"];
export type BackfillRequest = Schemas["BackfillRequest"];
export type BackfillPolicy = Schemas["BackfillPolicy"];
export type SelectionRequest = Schemas["SelectionRequest"];
export type SelectionResult = Schemas["SelectionResult"];
export type SelectionSummary = Schemas["SelectionSummary"];
export type InboxCreate = Schemas["InboxCreate"];
export type InboxUpdate = Schemas["InboxUpdate"];
export type RequestCreate = Schemas["RequestCreate"];
export type RequestRead = Schemas["RequestRead"];
export type RequestPage = Schemas["RequestPage"];
export type PruneRequest = Schemas["PruneRequest"];
export type PruneResult = Schemas["PruneResult"];
export type AboutRead = Schemas["AboutRead"];
export type ReadyRead = Schemas["ReadyRead"];
export type RebuildRequest = Schemas["RebuildRequest"];
export type Problem = Schemas["Problem"];
export type FeedHealth = Schemas["FeedHealth"];
export type CatalogCounts = Schemas["CatalogCounts"];
export type FeedCredentials = Schemas["FeedCredentials"];
export type ApiKeyRead = Schemas["ApiKeyRead"];
export type ApiKeyCreate = Schemas["ApiKeyCreate"];
export type ApiKeyCreated = Schemas["ApiKeyCreated"];
export type ApiKeyList = Schemas["ApiKeyList"];
export type EngineCookiesRead = Schemas["EngineCookiesRead"];
export type MirrorDefaults = Schemas["MirrorDefaults"];
export type VideoSearchPage = Schemas["VideoSearchPage"];
export type VideoSearchResult = Schemas["VideoSearchResult"];
export type EngineCookiesWrite = Schemas["EngineCookiesWrite"];

export type ArchiveState = Schemas["ArchiveState"];
export type AssetKind = Schemas["AssetKind"];
export type AssetState = Schemas["AssetState"];
export type BackfillMode = Schemas["BackfillMode"];
export type ErrorKind = Schemas["ErrorKind"];
export type FeedKind = Schemas["FeedKind"];
export type HealthStatus = Schemas["HealthStatus"];
export type JobKind = Schemas["JobKind"];
export type JobStatus = Schemas["JobStatus"];
export type JobTrigger = Schemas["JobTrigger"];
export type KeyScope = Schemas["KeyScope"];
export type Numbering = Schemas["Numbering"];
export type ProgressPhase = Schemas["ProgressPhase"];
export type RequestStatus = Schemas["RequestStatus"];
export type RequestedVia = Schemas["RequestedVia"];
export type SourceKind = Schemas["SourceKind"];

export type ItemSort = NonNullable<
  NonNullable<Operations["list_items"]["parameters"]["query"]>["sort"]
>;
export type SortOrder = "asc" | "desc";

export function isMirror(feed: FeedRead): feed is MirrorRead {
  return feed.kind === "mirror";
}

export function isInbox(feed: FeedRead): feed is InboxRead {
  return feed.kind === "inbox";
}
