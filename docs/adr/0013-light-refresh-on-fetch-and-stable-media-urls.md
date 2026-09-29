# 0013 A feed fetch runs a Light Refresh inline; media URLs are fixed at listing time

Status: accepted (v1.3); amends ADR 0012

## Context

Since 1.2 a `GET` of a Mirror Feed queued a worker Refresh and waited for it, up to 20 s,
so that new Episodes were in the answer. A Refresh is a full yt-dlp listing of the channel
(8 to 30 s), one per feed at a time, queued behind whatever archive jobs the worker is
running. Overcast crawls every feed about every two hours and an OPML import fetches every
feed at once. Measured in production on 2026-09-17: a feed fetch took a median 4.3 s and a
p90 of 21.5 s, and 21 % of them hit the cap. A first-page listing of the same channel
(`lazy_playlist`, fifteen entries) takes 0.3 s.

Download counting is exact per `GET` (ADR 0010), but an Automatic Mirror advertised
`{id}.mp3` for an item before its archive and `{id}.m4a` after it (ADR 0012). Podcast apps
treat a changed enclosure URL as a new file: every device downloaded the Episode again, and
five full downloads of one file within 20 s were observed.

The decisions taken with the operator: the fetch checks the Source inline without a long
wait; the scheduled full Refresh is unchanged; download counting stays exact per `GET`; only
the URL becomes stable.

## Decision

**Light Refresh.** A `GET` of a Mirror Feed (never a `HEAD`, never an Inbox, never a Paused
Mirror) runs a Light Refresh in the api process before rendering: a check of the Source's
newest items that adds and re-dates Catalog items, never Delists or renumbers, and never
waits on the worker. What it does is decided from the feed row alone:

- an RSS Source gets one conditional `GET` with the stored `ETag`/`Last-Modified`: a 304 is
  `unchanged` (the validators are stored, nothing else moves); a 200 is a complete listing,
  applied exactly as the worker applies one (Delisting included, sync deletions included,
  the Source XML snapshot rewritten);
- a YouTube channel, or any other Engine Source, gets a shallow listing of its first fifteen
  entries (`lazy_playlist`, `playlistend`) applied as a *partial* listing: known rows keep
  their Ordinal, Source numbering, tab and archivability and take only the refreshed text,
  duration and date; new rows are inserted normally; absent rows are not Delisted; the full
  listing on disk is left alone. A Source whose first page turns out to be its oldest items
  is not applied: the full Refresh is queued instead, as for a playlist;
- a YouTube playlist (which lists oldest first, so a first page is not its newest items)
  queues the normal full Refresh job instead and the feed answers at once.

After a listing the policy runs as in a Refresh and archive jobs are queued for what it
wants. The feed's revision (and so its ETag) moves only when a complete listing was applied
or a partial one added or wanted something. The check is skipped within
`refresh.fetch_cooldown_minutes` of any Refresh, full or light (`last_light_refresh_at` is
stamped before the Source is asked, so concurrent fetches skip; a per-process lock keeps two
fetches of one feed from listing twice), and is capped at 8 s, after which the feed is served
as it is. Every failure is logged and recorded and the feed is served regardless. Light
Refreshes are `refresh_runs` rows flagged `light` with trigger `feed_fetch`; they never
touch `last_refresh_attempt_at`, `last_refresh_success_at` or `last_error`, so the Mirror's
health keeps describing the full Refresh. The service is not a capability: no route, no MCP
tool.

**Stable media URL.** The URL a feed advertises for an item is
`{base}/feeds/{feed}/media/{item_id}.{public_ext}` and `public_ext` is set once, when the
row is created: `m4a` for an Engine Source; for an RSS enclosure the container the Engine
will leave for its MIME type (else its URL's known extension): `mp3` and `m4a` as they are,
an MPEG-4 video container as `m4a`, anything else (Opus, Vorbis, FLAC, WAV) as the `mp3` it
is transcoded to; `mp3` when nothing is known. It never changes afterwards, whatever
container the archive produces. The enclosure's `type` is the archived file's MIME type once archived and
the one `public_ext` implies before; its `length` is the real size once archived and the
128 kbit/s estimate before. The media route serves an item under its public extension,
under the archived file's own extension and, for an Automatic feed only, under the `.mp3`
placeholder 1.2 advertised. `public_ext` lives in `feed.json` and survives a rebuild;
migration 0010 backfills it (archived rows from their file, else by Source kind).

**Request log.** The api writes one `http.request` line per request from a pure ASGI
middleware wrapped outermost, after the final body chunk went out, with method, path, query,
status, duration, bytes sent, content length, client address (first hop of
`X-Forwarded-For`), user agent, `Range`, request id and whether the response completed;
health probes are skipped and credentials are never logged. uvicorn's access log is off.
The numbers above came from such lines.

## Consequences

- A feed fetch is bounded by the 8 s deadline plus rendering, and creates no worker job
  except for playlists. The worker's queue no longer decides how fast a podcast app gets its
  feed.
- New Episodes of a channel are in the fetch that found them, but a fetch never Delists a
  channel's items or moves their numbering: only the scheduled or manual full Refresh does.
- An RSS Source is listed in full on every fetch that finds it changed; the conditional GET
  makes the common case one 304.
- Listings run on a small pool of their own (four threads), never on the loop's default
  executor that serves media and writes descriptors, and an RSS fetch is capped at the
  deadline; after the deadline a yt-dlp listing thread keeps running until yt-dlp honours
  the cancel token. The api serves the feed meanwhile and the run is recorded as failed.
- The cooldown now gates the Light Refresh; `request_refresh(feed_fetch)` keeps its own
  cooldown rule for the playlist path.
- The enclosure URL an app holds never changes, so an Episode is downloaded once per device.
  ADR 0012's placeholder consequence is amended: the placeholder is the item's stable URL,
  and the `.mp3` variant is served for Automatic feeds only, for apps that still hold it.
- `ItemRead` carries `public_media_url` (the URL the feed advertises) next to `media` (the
  real file for the UI); `MirrorRead` carries `last_light_refresh_at`, which the Mirror
  header shows as *Checked on fetch*.
- Download counting is unchanged (ADR 0010): a `GET` of either extension counts.
