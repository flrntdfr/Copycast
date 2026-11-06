# 0029 Storage quotas: at the limit, new downloads wait; nothing is deleted for you

Status: accepted (3.0.0); builds on ADR 0028 (usage is per Owner); leaves ADR 0019's
`StorageFull` pause (a full Object store or Scratch) untouched

## Context

With several Users on one Object store (ADR 0028), one of them can fill the disk for
everyone: a Backfill of a long channel, a burst of Uploads, an Ephemeral feed played through.
Decision D2 of the configuration and users plan chose to **block new downloads** at the
quota: running jobs finish, nothing is deleted on the User's behalf, and what was refused
retries by itself once there is room. Copycast already keeps `feeds.storage_bytes` per Feed
(media plus archived assets), so usage needs no new bookkeeping. The Engine knows a size
before it downloads for most yt-dlp sources (`filesize`, `filesize_approx`), never for an RSS
enclosure.

## Decision

**Usage and states.** A User's usage is `SUM(feeds.storage_bytes)` over their Feeds; Scratch,
Postgres, the Feed cache and staged Uploads are not usage. Their quota is their own `quota`
in `[[auth.users]]`, else `auth.default_quota` (`unlimited` unless set), in decimal units
(`"10 GB"`, `"500 MB"`, a number of bytes; GiB is refused). A removed User's quota is the
default. The state is `ok`, `near` from 90 % or `over` from 100 % (so 0 of 0 is over). Open and
legacy mode have one Owner with `default_quota`.

**A blocked item waits, still wanted.** The archive job checks the Owner after its not-wanted
and Paused checks, before it marks the item archiving:
- Owner `over`: the item stays `archive_state = wanted` with `blocked_reason = quota`, an
  `ItemEvent` is published in the same unit of work, and the job **succeeds** with
  `{"blocked": "quota"}`. No attempt is counted, no new `ArchiveState` or `ErrorKind` exists,
  and the Catalog shows "Over quota".
- Otherwise the fetch carries `max_bytes`, the room left. For a yt-dlp fetch a match filter
  compares the selected formats' size with it; a larger estimate raises `QuotaExceeded`, and
  the item is blocked the same way with the estimate in `blocked_bytes` (clamped to BIGINT).
  An unknown size passes. Direct (RSS) fetches are never estimated.
- `StorageFull` is never reused for quotas: it pauses archive claims for everyone.

**Only manual entry points refuse.** `archive_item` (the UI, MCP and a podcast app's first play
of an Ephemeral item), `archive_available`, `retry_failed` and `select_items` (not a dry run)
answer **`507 over-quota`** while the Owner is over, before anything is set wanted; otherwise
they clear the block of what they select and queue it as usual. Backfill, Follow, policy
switches, `create_mirror` and `update_mirror` selections and Light Refresh never refuse: they
queue, and the archive job blocks what does not fit. So `GET /feeds/{id}.xml` always answers,
and a quota never surprises a podcast app with an error on its feed.

**A first play over quota is a 507, at once.** On `/feeds/…/media` an Ephemeral item whose
Owner is over answers `507 over-quota` without `Retry-After` (a 503 would be retried
forever). When the archive job blocks the item instead, its `ItemEvent` wakes the waiting
request, which sees `blocked_reason` and answers 507 rather than holding the line for 50 s.
The detail is one generic sentence with no usage or quota figures: a Feed's credential opens
that Feed, not its Owner's account.

**Uploads count before they land.** `create_upload` takes a declared `size_bytes` and refuses
with 507 when it does not fit; the token `PUT` stops at the room left (and at the 4 GiB upload
limit), discards the partial file and answers 507; `add_uploads` and `create_serial_bucket`
check the whole batch before copying anything. Staged Uploads not yet added count against
that room (a received one by its size, a pending one by its declared size for an hour while
its PUT can still come, and not at all once that PUT failed or was refused), an Owner holds at
most 1000 of them (409 beyond), `delete_upload` gives one up at once, and expired ones are
swept hourly. Each Owner's staged uploads are counted from a `staged_uploads` row per upload
(migration 0018), never by listing the Object store. `create_upload`, the PUT's final check
and the batch checks run under a per-Owner transaction lock (`pg_advisory_xact_lock`), so
parallel calls never overshoot.

**Blocked items come back by themselves, without a storm.** Every scheduler tick (60 s), and
at the end of a prune job that freed bytes, `release_if_room(owner)` runs for each Owner with
blocked items: nothing for an Owner who is over or no longer configured; otherwise it walks at
most 20 blocked items in the order their archive priority gives (manual, Follow, Backfill),
releases those blocked with no estimate, releases those whose `blocked_bytes` fit the budget
(subtracting each), and skips the rest. A released item is unblocked, announced and queued.
An item whose estimate never fits waits with no job and no Engine call until a delete, a
purge, Sync, expiry or a larger quota at restart makes room.

**The overshoot is bounded, not zero.** A running job always finishes. Each archive job reads
the room left when it starts, so with the worker's `concurrency` N an Owner can end up over
by **up to one Episode per archive job running in parallel for them** (N, plus the on-demand
slot), plus RSS enclosures, which are never estimated. Per-Owner archive admission is not
serialised in 3.0; Upload admission is.

**It shows.** `GET /api/me` returns the caller's quota. `GET /api/status` has a `quota`
component named "Storage" ("3.2 GB of 10 GB (32 %)", or "3.2 GB used (unlimited)"; `warn`
when near, `error` when over, with a meter), and for an Admin session one fact per User,
removed ones included. The 2.x `storage` component keeps its id and is renamed "Object
store"; its instance-wide facts are an Admin's. The web UI's status dot turns amber or red
with the quota like any other component, and Catalog rows carry an "Over quota" badge.

## Consequences

- Nothing is ever deleted to make room: Retention (autoprune, Ephemeral expiry) works as
  before and frees space, and the User decides what else goes.
- An Owner near the limit keeps a Backfill queued for as long as it takes; the items show
  "Over quota" in the Catalog and leave it on their own.
- An Ephemeral feed of an Owner who is over keeps listing every item, but a first play
  answers 507 until there is room; already archived Episodes keep playing.
- Two Users mirroring one Source each pay for their copy (ADR 0028).
- Not built: a quota estimate for RSS enclosures (`enclosure length` is not parsed), counting
  Scratch, Postgres or the Feed cache, serialising archive admission per Owner, an MCP tool for
  the quota, and deleting anything on a User's behalf.
