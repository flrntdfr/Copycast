# 0014 Live streams are archived once their recording is published

Status: accepted (v1.3.1)

## Context

yt-dlp marks every video with a `live_status`: `is_upcoming`, `is_live`, `post_live` (the
stream ended but YouTube has not published the recording yet), `was_live`, `not_live`, or
nothing. A flat channel listing knows `is_upcoming`, `is_live` and `was_live`; only a full
extraction, the one an archive runs, knows `post_live`. Copycast ignored all of it: an
archive job that met a stream in progress recorded the live HLS from that moment until the
stream ended and archived the result as the Episode. Observed in production on 2026-09-29:
an 87-minute show archived as 8.4 MB. Once archived, the item was never touched again (ADR
0009), so the truncated recording stayed.

## Decision

**Listing.** `SourceListingItem` and the Catalog row carry `live_status` (domain enum
`LiveStatus`, migration 0011, `feed.json` and the rebuild included). An Engine item is
archivable only when it is not upcoming and not live; an RSS item keeps its enclosure rule
and no status. Both the complete listing of a Refresh and the partial one of a Light Refresh
write the status and refresh archivability, so a stream that ended flips at the next fetch
of the feed, not only at the next full Refresh. Nothing else changes: an item that is not
archivable is not wanted by Backfill or Follow, is refused by `archive_item` and a
selection, and is left out of an Automatic feed (`for_render` requires `archivable`); once
archivable, Follow picks it up on its own because `available_ids(first_seen_after=…)`
already looks at every archivable item first seen after the policy stamp, and an Automatic
feed lists it.

**Archive time.** The Engine installs a `match_filter` on every fetch of an Engine item
(never a direct or RSS fetch) that refuses `is_upcoming`, `is_live` and `post_live` before
a byte is downloaded and surfaces the refusal as `NotReady(EngineError)`, carrying the
status and, for an upcoming stream with a known start, how long to wait; yt-dlp re-raises a
filter's `DownloadCancelled` as a fresh instance, so the reason travels in the engine's
per-fetch hook state rather than on the exception. A stream that has not started has no
formats at all, so the YouTube extractor refuses it before the filter runs, with YouTube's
own words ("This live event will begin in 2 hours", "Premieres in 3 days"); the engine
recognises that refusal (`errors.upcoming_refusal`) and reports `NotReady(is_upcoming)` for
it too. The worker treats `NotReady` as neither transient nor permanent: the job is queued
again `LIVE_RETRY` (30 minutes) later, or at the stream's scheduled start when that is
later, never past the job's ceiling, without counting the attempt; the item goes back to
`wanted` with the reason as `last_error` and its `live_status` stored so the UI says why.
Once the job has waited `LIVE_WAIT_MAX` (48 h since it was created) the job and the item
fail for good ("still not published after 48 h"). That ceiling is measured in one place,
the archive job: it turns an expired `NotReady` into the `PermanentError` it raises, and
the runner trusts the error it gets (`NotReady` re-queues, `PermanentError` fails) without
a clock of its own, so the Catalog and the job never disagree. The re-queue delay is cut to
the budget left (`created_at + LIVE_WAIT_MAX - now`), so a start estimate beyond the
ceiling still gets the job its last try at the ceiling instead of past it.

**Requests.** An Inbox has no Refresh, so nothing would ever flip a pushed stream's
`archivable` once it is recorded. A Request therefore wants every stream it lists whatever
its status (`expand_request.want_streams` sets `archivable` while keeping `live_status`):
the item is Queued with its badge and its archive job waits as above. A Request for a
stream that has not started fails at expansion, since YouTube lists nothing for it: the
Request's error carries the reason yt-dlp logged ("yt-dlp extracted nothing from …: This
live event will begin in 2 hours") and the URL is pushed again once the stream is live or
recorded.

**UI.** `ItemRead.live_status`; the Catalog shows *Live*, *Upcoming* or *Recording being
processed* next to the state with the tooltip "Archived once the recording is published".

**Nothing is repaired.** An Episode archived from a stream while it was live keeps its
partial recording (ADR 0009: Copycast never deletes from a Mirror on its own). Delete it
and archive it again once the recording is published.

## Consequences

- A live stream is never archived as a fragment. The Mirror Feed lists the Episode once
  YouTube publishes the recording, at the latest one Refresh interval after that; a fetch of
  the feed gets it sooner through the Light Refresh.
- An archive that meets a stream in the `post_live` gap ties up nothing: it retries every 30
  minutes for two days. A stream whose recording YouTube never publishes (deleted, made
  private, a premiere that never happened) fails after 48 h with a permanent error; *Retry
  N failed* queues a new job and starts the wait over.
- The creation-time edge: a stream that is live when the Mirror is created is first seen
  before `policy_applied_at`, so Follow never wants it once it is recorded, and a Backfill
  of Everything or Latest N ran once at the first Refresh and does not run again. Such a
  stream stays Available after its recording is published and must be archived on demand
  (*Archive all Available…*, a selection, or `archive_item`). A Rolling Mirror wants it at
  the next Refresh when it falls inside the window; an Automatic Mirror lists it as soon as
  it is recorded and archives it when a podcast app asks.
- No policy wants an upcoming stream, so a Mirror never queues an archive job for one; a
  Request does (a playlist that contains one, or an Available item archived on demand once
  a listing said `is_upcoming`). The YouTube extractor refuses such a video (no formats yet)
  before the filter sees it; the engine reads that refusal as `NotReady(is_upcoming)` with
  no start estimate, so the job tries again every 30 minutes until the stream starts, then
  waits like any live one.
- A stream pushed into an Inbox while live is archived once its recording is published; one
  pushed before it starts is refused at expansion with YouTube's reason and must be pushed
  again later. Before 1.3.1 the first case archived a fragment and the second failed with a
  bare "extracted nothing".
- `feed.json` carries `live_status` and a rebuild restores it; a descriptor written before
  1.3.1 has none, so the rebuilt row starts with no status and the next listing fills it.
