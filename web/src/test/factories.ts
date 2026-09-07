/** Typed builders for API read models, shaped exactly like the generated schema. */
import type {
  AboutRead,
  ApiKeyRead,
  EngineCookiesRead,
  InboxRead,
  MirrorDefaults,
  ItemRead,
  JobRead,
  MirrorRead,
  ProbeCandidate,
  ProbeResult,
  Problem,
  PruneResult,
  ReadyRead,
  RequestRead,
  SelectionResult,
  VideoSearchResult,
} from "@/api/types";

let sequence = 0;
export function nextId(prefix = "id"): string {
  sequence += 1;
  return `${prefix}-${sequence.toString(16).padStart(4, "0")}`;
}

export function mirror(overrides: Partial<MirrorRead> = {}): MirrorRead {
  const id = overrides.id ?? nextId("mirror");
  return {
    id,
    kind: "mirror",
    title: "Example Podcast",
    description: null,
    artwork_url: null,
    feed_url: `http://localhost:8080/feeds/${id}.xml`,
    feed_credentials: null,
    episode_count: 3,
    storage_bytes: 12_345_678,
    revision: 1,
    created_at: "2024-01-10T10:00:00Z",
    source_url: "https://podcast.example/feed.xml",
    source_title: overrides.title ?? "Example Podcast",
    title_override: null,
    service: null,
    source_kind: "rss",
    language: null,
    preferred_language: null,
    min_duration_seconds: null,
    paused: false,
    follow: true,
    backfill: { mode: "all", latest_n: null, retention_days: null },
    engine_options: {},
    last_refresh_attempt_at: "2024-01-15T09:00:00Z",
    last_refresh_success_at: "2024-01-15T09:00:00Z",
    last_error: null,
    health: { status: "ok", reason: null },
    counts: { listed: 3, available: 2, delisted: 0, wanted: 0, archived: 3, failed: 0 },
    selection: null,
    ...overrides,
  };
}

export function inbox(overrides: Partial<InboxRead> = {}): InboxRead {
  const id = overrides.id ?? nextId("inbox");
  return {
    id,
    kind: "inbox",
    title: "Copycast",
    name: "Copycast",
    description: null,
    artwork_url: null,
    feed_url: `http://localhost:8080/feeds/${id}.xml`,
    feed_credentials: null,
    episode_count: 0,
    storage_bytes: 0,
    revision: 1,
    created_at: "2024-01-01T00:00:00Z",
    autoprune_days: null,
    request_count: 0,
    ...overrides,
  };
}

export function item(overrides: Partial<ItemRead> = {}): ItemRead {
  const id = overrides.id ?? nextId("item");
  const feedId = overrides.feed_id ?? "mirror-0001";
  const state = overrides.state ?? "archived";
  return {
    id,
    feed_id: feedId,
    ordinal: 1,
    source_number: null,
    source_season: null,
    title: `Episode ${id}`,
    description: "<p>Show notes</p>",
    published_at: "2024-01-12T08:00:00Z",
    added_at: "2024-01-12T09:00:00Z",
    duration_seconds: 1800,
    state,
    listed: true,
    attempt_count: 0,
    last_error: null,
    media:
      state === "archived"
        ? {
            url: `http://localhost:8080/feeds/${feedId}/media/${id}.m4a`,
            bytes: 4_407,
            mime: "audio/mp4",
            ext: "m4a",
          }
        : null,
    artwork_url: null,
    assets: [],
    download_count: 0,
    first_downloaded_at: null,
    last_downloaded_at: null,
    request_ids: [],
    item_url: "https://podcast.example/episodes/1",
    ...overrides,
  };
}

export function job(overrides: Partial<JobRead> = {}): JobRead {
  return {
    id: overrides.id ?? `00000000-0000-4000-8000-${(++sequence).toString().padStart(12, "0")}`,
    kind: "refresh",
    feed_id: "mirror-0001",
    item_id: null,
    request_id: null,
    trigger: "manual",
    status: "queued",
    progress: null,
    result: null,
    error: null,
    error_kind: null,
    attempt: 0,
    created_at: "2024-01-15T09:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

export function candidate(overrides: Partial<ProbeCandidate> = {}): ProbeCandidate {
  return {
    candidate_token: overrides.candidate_token ?? nextId("tok"),
    source_url: "https://podcast.example/feed.xml",
    source_kind: "rss",
    service: null,
    title: "Example Podcast",
    description: null,
    artwork_url: null,
    author: "Fixture Media",
    item_count: 6,
    ...overrides,
  };
}

export function probeResult(
  candidates: ProbeCandidate[],
  inputUrl = "https://podcast.example/",
): ProbeResult {
  return { input_url: inputUrl, candidates };
}

export function selectionResult(overrides: Partial<SelectionResult> = {}): SelectionResult {
  return {
    resolved: [],
    unresolved: [],
    numbering_used: "source",
    already_archived_count: 0,
    jobs: [],
    dry_run: false,
    ...overrides,
  };
}

export function request(overrides: Partial<RequestRead> = {}): RequestRead {
  return {
    id: overrides.id ?? `00000000-0000-4000-8000-${(++sequence).toString().padStart(12, "0")}`,
    inbox_id: "inbox-1",
    url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    requested_via: "ui",
    status: "queued",
    item_count: 0,
    error: null,
    items: [],
    job: null,
    created_at: "2024-01-15T09:00:00Z",
    ...overrides,
  };
}

export function about(overrides: Partial<AboutRead> = {}): AboutRead {
  return {
    version: "1.0.0",
    engine: {
      name: "yt-dlp",
      version: "2026.8.19",
      channel: "nightly",
      release_date: "2026-08-19",
      git_head: "abc1234",
    },
    ffmpeg_version: "8.1.2",
    base_url: "http://localhost:8080",
    layout_version: "1",
    totals: { feeds: 2, episodes: 42, storage_bytes: 1_500_000_000 },
    auth_enabled: false,
    ...overrides,
  };
}

export function apiKey(overrides: Partial<ApiKeyRead> = {}): ApiKeyRead {
  return {
    id: overrides.id ?? `00000000-0000-4000-8000-${(++sequence).toString().padStart(12, "0")}`,
    name: "Claude Code",
    scope: "write",
    prefix: "cck_a1b2c3d4",
    created_at: "2024-01-15T09:00:00Z",
    last_used_at: null,
    ...overrides,
  };
}

export function engineCookies(overrides: Partial<EngineCookiesRead> = {}): EngineCookiesRead {
  return {
    present: false,
    size_bytes: 0,
    updated_at: null,
    cookie_count: 0,
    domains: [],
    youtube: false,
    ...overrides,
  };
}

export function mirrorDefaults(overrides: Partial<MirrorDefaults> = {}): MirrorDefaults {
  return {
    language: null,
    min_duration_seconds: null,
    backfill: { mode: "automatic", latest_n: null, retention_days: 7, selection: null },
    ...overrides,
  };
}

export function videoResult(overrides: Partial<VideoSearchResult> = {}): VideoSearchResult {
  return {
    title: "WWDC 2024 Live from Cupertino",
    url: "https://www.youtube.com/watch?v=abc123",
    channel: "The Talk Show",
    duration_seconds: 5400,
    published_at: "2024-06-11T20:00:00Z",
    artwork_url: null,
    ...overrides,
  };
}

export function ready(overrides: Partial<ReadyRead> = {}): ReadyRead {
  return {
    status: "ok",
    checks: {
      db: { ok: true, detail: null },
      schema: { ok: true, detail: "0001" },
      data_dir: { ok: true, detail: null },
      layout: { ok: true, detail: "1" },
      worker_seen_at: { ok: true, detail: "2024-01-15T09:00:00Z" },
    },
    ...overrides,
  };
}

export function pruneResult(overrides: Partial<PruneResult> = {}): PruneResult {
  return { matched: 0, deleted_count: 0, bytes_freed: 0, dry_run: true, ...overrides };
}

export function problem(
  slug: string,
  status: number,
  extra: Record<string, unknown> = {},
): Problem & Record<string, unknown> {
  return {
    type: `urn:copycast:problem:${slug}`,
    title: slug.replace(/-/g, " "),
    status,
    detail: `Problem ${slug}`,
    instance: null,
    ...extra,
  };
}
