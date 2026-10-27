# 0019 Every byte lives in an Object store; the worker transcodes in Scratch; feeds are cached shared

Status: accepted (v2.0); amends ADR 0001, narrows ADR 0012's on-demand wait

## Context

Overcast never showed a download progress bar for an Automatic (since ADR 0016:
Ephemeral) Mirror's Episode, and
streaming one failed. The media route archived an unarchived item on the first request and
held the request open for up to 120 s with no byte sent, then answered 503. The archive is a
yt-dlp download followed by post-processing that rewrites the whole file (normalisation,
`FFmpegMetadataPP`, the thumbnail embedder), so nothing could be served before it finished.
iOS drops a request that receives nothing for 60 s, and AVPlayer's streaming probe gives up
sooner. The client was gone before the 503, so it never saw headers, a length or a retry.
A first play also queued behind every Backfill and Refresh of the same feed, because of
per-feed job exclusivity and two worker slots.

The operator decided, on 2026-10-27:

- every byte goes to an S3 bucket, and Postgres can be rebuilt from it;
- S3 only, no filesystem backend;
- Copycast proxies objects (no presigned redirects);
- the first request holds the line and is then served the finished file, bit-perfect, with
  all its metadata (no raw pass-through);
- archives a podcast app waits for get a worker slot of their own;
- rendered feeds go in a memcached-like shared cache, so the api can scale out.

MinIO was the operator's first choice, but it no longer publishes images (its Docker Hub
repository answers 404), so nothing maintained could be pinned by digest.

## Decision

**The Object store.**
- `[storage] url = "s3://<bucket>[/<prefix>]"` holds every byte Copycast keeps, under the
  keys the data directory used:
  - `LAYOUT_VERSION` (now `2`)
  - `engine/cookies.txt`, `engine/defaults.json`
  - `feeds/{id}/feed.json`
  - `source/…`, `media/…`, `assets/…`
- Postgres keeps storing paths relative to `feeds/{id}/`. A single PUT is atomic in S3, so
  descriptors, listings and assets are one PUT each. Media goes up as a multipart upload of
  16 MiB parts.
- `copycast rebuild` lists each feed's prefix once and rebuilds from that listing.
- The client is obstore, through one small `ObjectStore` (`adapters/storage/objects.py`).
- `memory://<name>` is the same code over a process-local store, for tests and `copycast
  openapi`. It is never durable.
- The compose stack ships SeaweedFS (Apache-2.0) with one identity from the environment,
  anonymous access refused, and the bucket created on first start. Any S3 server works.
- A layout-1 data directory moves with `copycast storage push --from DIR`. It is resumable
  and checks every object's size, and it writes `LAYOUT_VERSION` 2 only when every file has
  landed. Until then both processes refuse the Object store.

**Scratch.**
- `[storage] scratch_dir` is a local directory the Object store never sees.
- An archive job works in `jobs/{item}.{random}/`: yt-dlp's `home` and `tmp`, normalisation,
  tagging, artwork.
- Only the finished files leave Scratch, uploaded before the row is marked `archived`, so no
  row points at a missing object and nothing raw or partial is ever served.
- The directory goes when the job ends, whatever happened, and a starting worker sweeps
  leftovers. A retried job downloads again (no cross-attempt resume).
- The cookie file reaches yt-dlp as a private 0600 copy in Scratch, written back when yt-dlp
  rotated it.

**Serving.**
- Media and assets are proxied with `ObjectResponse`. Its HTTP semantics are `FileResponse`'s,
  with Starlette's own Range parser: 200/206 (multipart too), 416, 400, `If-Range`, the
  object's strong ETag, `Content-Length` and `Last-Modified`.
- Every read is pinned to the ETag `head` saw (`If-Match`), so an object replaced
  mid-response ends the body instead of splicing two versions.
- Download counting (ADR 0010) and Feed credentials are unchanged.

**First play.**
- A request for an unarchived item of an Ephemeral Mirror queues the archive and holds the
  line, with no byte sent. It is woken by the item's event (Postgres NOTIFY through the api's
  EventHub, re-checked every 5 s in case an event is missed), then serves the finished object.
- The hold ends at 50 s, under iOS's 60 s idle limit, with `503 Retry-After: 30`; the archive
  keeps running. `HEAD` holds the same way, because a guessed `Content-Length` could not be
  bit-perfect.
- The worker keeps one slot (`ON_DEMAND_SLOTS`) beyond `concurrency` for archives with
  `trigger=feed_fetch`.
- Such an archive is exempt from per-feed exclusivity: it waits only for another job of the
  same item.

**Feed cache.**
- `[cache] url` names a Valkey (or Redis) every api replica shares.
- Bodies are keyed by `(feed_id, revision)` with a TTL, so a stale body is never served.
- The per-process LRU stays in front.
- A render takes a short `SET NX` lock, and a replica that loses it waits up to 2 s for the
  winner's body. Any cache error is logged and the request renders as if the cache were
  absent.
- Valkey rather than memcached: memcached's default 1 MiB item limit is below a large
  channel feed with show notes.

## Consequences

- Backups are the Object store (plus Postgres for telemetry, as before). ADR 0001's "the data
  directory is complete enough to rebuild" now reads "the Object store".
- The api holds no state of its own: it can run several replicas. On Kubernetes the worker no
  longer needs to be pinned to the api's node, and the data volume is gone.
- Every served byte passes through the api, which is the bandwidth cost of keeping Feed
  credentials and exact Download counts. On the same host, the S3 hop is about a millisecond
  per request.
- An archive that takes longer than 50 s still answers 503 to its first request. The archive
  is now as fast as the Source allows, and the next tap is served at once. Sending
  bit-perfect early bytes would need Copycast's own ID3 writer for mp3 Sources (the tag is
  known before the download and has an exact length); it is not done.
- Tests run on a per-test `memory://` store. `COPYCAST_TEST_S3_URL` runs the whole suite
  against a real server, and the `s3` tier runs SeaweedFS in a testcontainer.
- New pinned dependencies: obstore, redis. New images: SeaweedFS, Valkey.
