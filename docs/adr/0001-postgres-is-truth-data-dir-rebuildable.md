# 0001 Postgres is the runtime truth; the data directory is rebuildable

Status: accepted (v1)

## Context

The previous version kept every piece of state in files. That made backups trivial but made
listing, filtering, paging, per-feed exclusivity and progress reporting awkward, and every
request walked the disk. v1 needs a Catalog with thousands of items per feed, job queues
with claims and retries, and live events. At the same time the archive must outlive any
database accident: the audio and its metadata are the product.

## Decision

Postgres 17 is authoritative at runtime for metadata and intent (feeds, catalog items,
assets, requests, jobs, telemetry). The data directory holds the media, the sidecars
(`.info.json`, the original RSS `<item>`), the verbatim Source XML, the assets, and one
`feed.json` descriptor per feed re-exported after every change of intent. `copycast rebuild`
reconstructs the database from the data directory alone; it loses only telemetry (jobs and
their logs, refresh runs, heartbeats, engine version history, download counters). A
`LAYOUT_VERSION` file guards the on-disk format and a mismatch refuses to start.

Item ids are deterministic hashes of the feed id and the Source key, so a rebuild reproduces
them and feed URLs, media URLs and podcast-client state survive.

## Consequences

- Backups are the data directory; `pg_dump` is optional and only preserves telemetry.
- No request handler walks the disk; storage sizes are recounted after file-touching jobs.
- Every intent change must go through a UnitOfWork whose after-commit hook exports the
  descriptor; forgetting it makes a rebuild lose that change.
- Two counters live on every feed: `intent_version` guards the descriptor export and
  `revision` is the published-feed change token (ETag, cache key, SSE).
