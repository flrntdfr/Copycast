# Object storage, scratch transcoding and a shared feed cache

Status: built on branch `object-storage` (from 3551244), 2026-10-27; ADR 0019 records the
decision. It replaces the data directory with an S3 Object store, gives the worker a
Scratch directory, and puts rendered feeds in a shared Feed cache. The open decisions of §7
were settled with the recommended answers (SeaweedFS; an S3 test tier plus `memory://`;
one-way `storage push`; WIP kept apart and rebased later; Light Refresh stays inline;
storage totals from Postgres). The S3 store is called the **Object store**, not the Bucket:
main has since given "Bucket" to a Feed's own storage (its `feeds/{id}/` prefix here).

## 1. The Overcast bug, as the code shows it

Production logs were not read for this; the diagnosis is from the code and should be
checked against `http.request` lines with `user_agent` Overcast (`range`, `duration_ms`,
`status`, `completed`) before and after the change.

- An unarchived item of an Automatic feed is served by `_archive_on_demand`
  (`adapters/api/routes/public.py`): `GET` **and `HEAD`** queue the archive, then poll
  every 2 s for up to 120 s with no byte sent, then answer `503 Retry-After: 30`.
- The archive cannot be served earlier: yt-dlp downloads the whole file, the audio
  normaliser may transcode, `FFmpegMetadataPP` and the thumbnail embedder rewrite the
  whole file, and only then is it moved under `media/`.
- iOS `URLSession` drops a request after 60 s without bytes (its default
  `timeoutIntervalForRequest`); AVPlayer's streaming probe (`HEAD`, or
  `Range: bytes=0-1`) gives up sooner. Overcast therefore gets no headers at all, so no
  Content-Length, so no progress bar, and streaming fails. The 503 at 120 s arrives
  after the client has already gone, so it is never seen.
- Archived Episodes are a `FileResponse` with Content-Length, Range and a strong ETag;
  gzip skips `audio/*`. That path looks correct. If the progress bar is also missing
  there, the logs will show a second bug.

## 2. What the user decided (2026-10-27)

| | Decision |
|---|---|
| Serving | Copycast proxies the object (Range passed through). The bucket stays private; feed credentials and download counting do not change. |
| First play | The request holds the line and starts serving once the object is in the bucket, **bit-perfect, with all metadata embedded** (no raw pass-through). |
| Object store scope | **Everything**: the bucket alone can rebuild Postgres. A memcached-like shared cache keeps rendered feeds in memory. Goal: fast, and able to scale out. |
| Backends | **S3 only.** The filesystem backend goes away. |

### The physical limit of "bit-perfect and as soon as possible"

The first bytes of the final file depend on the whole post-processing run: the ID3v2 tag
of an mp3, and the `moov` atom of an m4a with artwork and chapters, are written last and
their size is unknown until then. So no byte, and no Content-Length, can be sent before
the archive is finished. Holding the line therefore helps only while the archive takes
less than the client's patience (about 60 s for Overcast). The plan does two things:
it makes the on-demand archive as fast as possible (§4), and it answers before the
client gives up (§5.2). A later option that keeps bit-perfection for mp3 is in §8.

## 3. Object store layout (layout version 2)

The keys are today's paths, so `rebuild`, the descriptor and the asset naming rules keep
their meaning:

```
LAYOUT_VERSION                                  "2\n"
engine/cookies.txt, engine/cookies/{name}.txt   secrets; the bucket is private, SSE on
engine/defaults.json
feeds/{feed_id}/feed.json                       descriptor (rebuild reads it)
feeds/{feed_id}/source/feed.xml | listing.json
feeds/{feed_id}/media/{item_id}.{ext}           audio, .info.json, .item.xml
feeds/{feed_id}/assets/...                      Artwork, chapters, transcripts, Attachments
uploads/{upload_id}                             Serial Inbox Uploads (swept after 24 h)
```

- A single-object PUT is atomic in S3, so `write_atomic` and `write_json_atomic` become
  `put`. Media goes up as multipart parts of 16 MiB, uploaded in parallel.
- `tmp/` leaves the bucket entirely: it is the worker's scratch (§4).
- `recount_storage` sums a `list` of the feed prefix (or the sizes Postgres already
  records, see §7 O6).
- The `ObjectStore` port goes in `application/ports.py`: `get` (with range), `head`,
  `put`, `put_file` (multipart), `delete`, `delete_prefix`, `list`. One adapter,
  `adapters/storage/s3.py`. The proposed client is `obstore` (Rust, natively async,
  typed, with ranged and streamed GETs and multipart PUT); `aiobotocore` is the fallback
  if obstore's typing does not satisfy pyright strict.
- About 25 modules use `Layout` today (storage, rebuild, descriptor, chapters, the
  archive and refresh jobs, uploads, defaults, cookies, health, about). `Layout` becomes
  a key builder over the store, and every `Path` operation becomes a store call.

## 4. Worker: download, transcode and finish in scratch, then upload

- New setting `COPYCAST__STORAGE__SCRATCH_DIR`: a local partition for the worker only
  (compose: a named volume or tmpfs; Kubernetes: an `emptyDir`, optionally
  `medium: Memory`). The api needs none.
- `archive_item`: the Engine works entirely in `scratch/{job_id}/` (download, normalise,
  embed metadata and artwork). The finished file then goes to the bucket with
  multipart; sidecars and assets follow; the item is marked `archived` only once its
  media object exists. Only the finished, fully processed file ever leaves scratch:
  no raw or partial bytes are ever served. The scratch directory is removed in `finally`. A worker that
  starts sweeps any leftover `scratch/*`.
- `continuedl` resumes are scratch-local, so a retried job resumes only on the same
  worker. That is acceptable.
- `StorageFull` keeps its meaning for scratch (free space checked before the fetch,
  using the format's `filesize` when known).
- **On-demand lane**: jobs with `trigger=feed_fetch` get one reserved worker slot, so an
  app's request is never queued behind a Backfill. Today two busy slots can delay it by
  minutes.
- **Wake, don't poll**: the api already listens to Postgres NOTIFY (EventHub). A waiting
  media request subscribes to the item's `archived` event instead of polling every 2 s.

## 5. Serving

### 5.1 Archived Episode (the object exists)

`GET|HEAD /feeds/{id}/media/{item}.{ext}`:

- `head_object`, then a ranged `get` streamed to the client in 256 KiB chunks.
- `Content-Length`, `Accept-Ranges`, the strong `ETag` (the object's ETag),
  `Last-Modified`, and `206`/`416`/`304` with `If-Range` and `If-None-Match`, answered
  by Copycast exactly as Starlette's `FileResponse` does today.
- Assets get the same treatment.
- Download counting (ADR 0010) is unchanged.
- Golden tests: byte-identical bodies and headers against the `FileResponse` behaviour
  the current integration tests already pin.

### 5.2 Not archived yet (Automatic feed)

- The request holds the line, with no bytes sent, until the item's `archived` event
  arrives, then serves as in §5.1 (bit-perfect, metadata embedded).
- The hold ends at `ON_DEMAND_WAIT_SECONDS`, which becomes **50 s** (configurable), so
  the `503 Retry-After` reaches the client before iOS gives up at 60 s. Today it is
  120 s, past that point.
- `HEAD` holds exactly like `GET`: a made-up Content-Length would break bit-perfection.
- The first request starts the archive in the background (download and processing in
  scratch, on the reserved on-demand slot) and waits on it, so it is served in that same
  request whenever the archive finishes inside the hold. An archive that outlives the
  hold keeps running; the next tap gets the file at once.

## 6. Shared feed cache

- Rendered feed bodies go into a shared cache keyed by `feed:{feed_id}:{revision}`,
  with the weak ETag and Last-Modified alongside. The per-process `FeedCache` stays as
  an L1 in front, so a hit costs no network round-trip.
- Every api replica sees the same rendered body. The renderer runs once per revision
  across the fleet, with a `SET NX` render lock so a burst of Overcast crawls renders
  once.
- **Valkey rather than memcached**: memcached's default item limit is 1 MiB, and a large
  channel feed with full show notes exceeds it. Valkey (BSD, the Redis fork) has no such
  limit, works with the async `redis` client, and needs no persistence here. The cache
  is optional: with `COPYCAST__CACHE__URL` unset, only L1 runs.
- Not solved by the cache: a Mirror Feed `GET` still runs the inline Light Refresh (up
  to 8 s, ADR 0013). If the goal is crawl latency, that is the larger lever (for example
  answering from cache and running the Light Refresh after the response). See O5.

## 7. Open decisions

- **O1, S3 server image.** The `minio/minio` Docker Hub repository now returns 404:
  MinIO stopped publishing community images in 2025, and the source repository is in
  maintenance. AGENTS.md pins every image by digest, and an unmaintained image gets no
  CVE fixes. Options:
  - **(a) SeaweedFS** (Apache-2.0, mature, S3 with multipart and Range): recommended.
  - (b) Garage (AGPL, small, stable, enough S3 for this).
  - (c) RustFS (Apache-2.0, MinIO-compatible, young).
  - (d) Build MinIO from its last source release in CI and publish it to GHCR ourselves.

  Copycast speaks plain S3 either way, so the choice can change later.
- **O2, tests.** S3-only means every storage, worker, api and MCP test needs S3. The
  proposal is a session-scoped S3 testcontainer with one bucket per test (like the
  per-test Postgres database) and `COPYCAST_TEST_S3_URL` to skip testcontainers. The
  test suite gets slower, and the 26 test modules that use `layout` are rewritten.
- **O3, migrating 3.dufour.xyz.** `copycast storage push --from /data` uploads an
  existing layout-1 data directory (verified by size and checksum), then writes
  `LAYOUT_VERSION` 2. The api refuses layout 1 when S3 is configured, with one
  actionable line. Is a one-way cut acceptable, keeping `data/` as the rollback until
  you delete it?
- **O4, release and branch.** The working tree carries uncommitted Serial Inbox and
  player work, and this change touches the same files (`layout.py`, `descriptor.py`,
  `rebuild.py`, `archive_item.py`). Proposal: commit that work first, then build this on
  its own branch as the next minor release, ahead of the 1.5 multi-user steps.
- **O5, Light Refresh after the response.** Should a Mirror Feed `GET` answer from cache
  at once and run the Light Refresh afterwards (new items appear on the next fetch)? This
  would change ADR 0013's "new items are in the answer".
- **O6, storage totals.** Sum sizes from Postgres (`media_bytes`, `size_bytes`), or list
  the bucket? Postgres is free and already exact for media; `rebuild` lists the bucket
  anyway.

## 8. Later: bit-perfect early bytes for mp3

For an mp3 Source the final file is `ID3v2 tag + the Source's MPEG frames` (its own tag
stripped). The tag (title, artist, artwork, CHAP chapters) is known before the download
and has an exact length, and the frames' length is the Source's Content-Length minus its
own tag. So Copycast could send `200` with the exact final Content-Length at once and
stream the file while it downloads, byte-identical to the archived object. That needs
our own tag writer instead of `FFmpegMetadataPP` for mp3. It does not work for m4a,
which ffmpeg remuxes.

## 9. Work breakdown

1. ADR 0019 (amends 0001, narrows 0013's media serving) and the Language section:
   **Object store**, **Scratch**, **Feed cache**.
2. The `ObjectStore` port and the S3 adapter; settings `[storage] endpoint, bucket,
   region, access_key, secret_key, scratch_dir` and `[cache] url`; the S3 testcontainer
   fixture.
3. `Layout` over the store; descriptor, rebuild, chapters, defaults, cookies, Uploads
   and health ported.
4. The archive job in scratch, the multipart upload, the on-demand lane, and the
   NOTIFY-based wait.
5. Media and asset proxying with Range and conditionals; golden header tests.
6. The Valkey feed cache with L1 and the render lock.
7. Compose (S3 server, Valkey, scratch volume, bucket init), Kubernetes base,
   `docs/operations.md`, and `copycast storage push`.
8. `make lint typecheck test`, smoke against compose, then a staged deploy with log
   checks in Overcast.
