/**
 * Every capability the UI consumes, mapped to `[method, path]`; a vitest checks each
 * entry against `x-capability` in openapi.json so the screens never drift from the API.
 */
import type { paths } from "./schema";

export type HttpMethod = "get" | "post" | "put" | "patch" | "delete";

type PathsFor<M extends HttpMethod> = {
  [P in keyof paths]: paths[P] extends Record<M, unknown> ? P : never;
}[keyof paths];

type Op<M extends HttpMethod = HttpMethod> = M extends HttpMethod
  ? readonly [M, PathsFor<M>]
  : never;

export const OPS = {
  list_feeds: ["get", "/api/feeds"],
  get_feed: ["get", "/api/feeds/{feed_id}"],
  delete_feed: ["delete", "/api/feeds/{feed_id}"],
  rotate_feed_credentials: ["post", "/api/feeds/{feed_id}/credentials/rotate"],
  list_items: ["get", "/api/feeds/{feed_id}/items"],
  get_item: ["get", "/api/feeds/{feed_id}/items/{item_id}"],
  archive_item: ["post", "/api/feeds/{feed_id}/items/{item_id}/archive"],
  delete_item: ["delete", "/api/feeds/{feed_id}/items/{item_id}"],
  probe_source: ["post", "/api/probe"],
  search_podcasts: ["get", "/api/search/podcasts"],
  search_videos: ["get", "/api/search/videos"],
  create_mirror: ["post", "/api/mirrors"],
  update_mirror: ["patch", "/api/mirrors/{feed_id}"],
  set_paused: ["post", "/api/mirrors/{feed_id}/pause"],
  request_refresh: ["post", "/api/mirrors/{feed_id}/refresh"],
  select_items: ["post", "/api/mirrors/{feed_id}/selections"],
  preview_mirror_update: ["post", "/api/mirrors/{feed_id}/preview"],
  archive_available: ["post", "/api/feeds/{feed_id}/archive-available"],
  retry_failed: ["post", "/api/feeds/{feed_id}/retry-failed"],
  create_inbox: ["post", "/api/inboxes"],
  update_inbox: ["patch", "/api/inboxes/{inbox_id}"],
  add_request: ["post", "/api/inboxes/{inbox_id}/requests"],
  list_requests: ["get", "/api/inboxes/{inbox_id}/requests"],
  get_request: ["get", "/api/inboxes/{inbox_id}/requests/{request_id}"],
  prune_inbox: ["post", "/api/inboxes/{inbox_id}/prune"],
  list_jobs: ["get", "/api/jobs"],
  get_job: ["get", "/api/jobs/{job_id}"],
  cancel_job: ["post", "/api/jobs/{job_id}/cancel"],
  subscribe_events: ["get", "/api/events"],
  about: ["get", "/api/about"],
  rebuild: ["post", "/api/admin/rebuild"],
  purge_episodes: ["post", "/api/admin/purge"],
  list_api_keys: ["get", "/api/keys"],
  create_api_key: ["post", "/api/keys"],
  revoke_api_key: ["delete", "/api/keys/{key_id}"],
  get_engine_cookies: ["get", "/api/engine/cookies"],
  set_engine_cookies: ["put", "/api/engine/cookies"],
  delete_engine_cookies: ["delete", "/api/engine/cookies"],
  get_mirror_defaults: ["get", "/api/settings/defaults"],
  set_mirror_defaults: ["put", "/api/settings/defaults"],
} as const satisfies Record<string, Op>;

export type Capability = keyof typeof OPS;

/** Routes without a capability that the UI still calls. */
export const EXTRA_OPS = {
  resume_mirror: ["post", "/api/mirrors/{feed_id}/resume"],
  health_ready: ["get", "/healthz/ready"],
} as const satisfies Record<string, Op>;

export function opOf<C extends Capability>(capability: C): (typeof OPS)[C] {
  return OPS[capability];
}
