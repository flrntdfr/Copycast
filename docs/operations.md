# Operating Copycast

This is the runbook: first run, configuration, the Object store, Kubernetes, backup and
restore, rebuilding the database from the Object store, and engine bumps and rollbacks. The
product language (Mirror, Bucket,
Refresh, ...) is defined in [AGENTS.md](../AGENTS.md).

## Shape of a deployment

One image, `ghcr.io/flrntdfr/copycast`, run as two processes plus Postgres 17, an S3 bucket
(the Object store; SeaweedFS in the compose stack) and, optionally, Valkey (the shared Feed cache):

| Process | Command | Port | Role |
|---|---|---|---|
| api | `copycast api` | 8080 | web UI, HTTP API, MCP at `/mcp`, published feeds and media, SSE events |
| worker | `copycast worker` | 8081 (health only) | Refreshes, downloads through the engine, Request expansion, pruning, rebuilds |

Both share the same Postgres database and the same Object store (ADR 0019). The worker downloads,
transcodes and tags in its local Scratch directory (`/scratch` in the image) and uploads only
finished files; the api proxies objects to podcast apps and holds no state of its own, so it
can run several replicas (its own Scratch only holds an Upload while its tags are read). There is exactly one worker: it holds a session-level advisory lock and a second worker exits
with a message. By default Copycast has no authentication: put it behind Tailscale (the
compose `tailscale` profile, the Kubernetes Tailscale Ingress) or on a trusted network, or
switch authentication on (below) behind TLS.

## First run with Docker Compose

```bash
cp .env.example .env            # set POSTGRES_PASSWORD, COPYCAST_S3_SECRET_KEY, COPYCAST_BASE_URL
mkdir -p config
docker compose up -d
docker compose logs -f api worker
```

`.env` chooses the profile (`tailscale` or `direct`), the Postgres password, the Object store's
secret key (shared by SeaweedFS and Copycast; `COPYCAST_S3_DATA` binds the archive to a
directory instead of the `seaweedfs` volume) and the public base URL. The base URL is embedded in every feed and media link, so it must be the address
podcast clients will use: `https://copycast.<tailnet>.ts.net` for the Tailscale profile,
`http://<host>:8080` for the direct profile.

Both processes exit with code 2 and a single actionable line when the configuration is
unusable: an Object store that cannot be reached or refuses the keys, a `LAYOUT_VERSION` mismatch
(a 1.x data directory not yet pushed, below), an unwritable Scratch directory (the line
contains the `chown` command to run), a rejected engine option, or a malformed base URL.

Readiness: `GET /healthz/ready` on the api (8080) and on the worker (8081) answers 200 with
`{status, checks{db, schema, storage, layout, worker_seen_at}}` (the worker adds
`scratch`, `scheduler` and `ffmpeg`), 503 when anything is degraded. `GET /healthz/live` is a plain
liveness check. `/api/about` shows the app version, the engine version and channel, ffmpeg,
the layout version and storage totals.

Status: `GET /api/status` (the MCP tool `get_status`, and the dot in the web UI's header)
reports each component as ok, warn, error or unknown with a summary and details: the
Postgres, the worker (from its heartbeat), the scheduler, the job queue, storage, yt-dlp
(warns when the worker runs another version or the release is over 60 days old), ffmpeg,
and the crawlers, that is the last 24 h of Refreshes by service with each one's last error.
It sits behind the operator password and never reaches the internet; probes keep using
`/healthz/ready`.

## Configuration

Configuration is read, in increasing precedence, from built-in defaults, the TOML file named
by `COPYCAST_CONFIG`, and environment variables. A missing TOML file is tolerated.

### `config/copycast.toml`

```toml
base_url     = "http://localhost:8080"
database_url = "postgresql+psycopg://copycast:copycast@localhost:5432/copycast"   # postgresql:// is rewritten

[refresh]
# interval_hours: ignored since 1.2; the Refresh interval is a Settings-page default
fetch_cooldown_minutes = 5      # a feed fetch skips its Light Refresh this soon after any Refresh
concurrency            = 2      # parallel engine jobs in the worker

[engine]
channel = "nightly"             # informational: nightly | stable

[engine.options]                # raw yt-dlp defaults applied to every job

[storage]                       # the Object store (ADR 0019); prefer the environment for the keys
url               = "s3://copycast"   # s3://<bucket>[/<prefix>]; memory://<name> for tests only
endpoint          = ""                # empty for AWS S3; http://seaweedfs:8333 in compose
region            = "us-east-1"
access_key_id     = "..."             # or the usual AWS_* environment
secret_access_key = "..."
scratch_dir       = "./scratch"       # local: downloads and transcodes, never served
min_free_scratch_mb = 512
request_timeout_seconds = 60          # every Object store call but a served stream
stream_timeout_seconds  = 21600       # one GET streamed to a podcast app (a slow app reads
                                      # a long Episode for many minutes; this bounds a stall)

[cache]                         # the shared Feed cache; unset, each api process caches alone
url = "redis://valkey:6379/0"   # redis://, rediss://, valkey:// or valkeys://
ttl_seconds = 86400

[auth]                          # prefer the environment for the password (below)
username = "copycast"           # the operator's username
# password = ""                 # 8+ characters switches authentication on
```

Every TOML key can be overridden with `COPYCAST__<SECTION>__<KEY>` (double underscores):
`COPYCAST__BASE_URL`, `COPYCAST__DATABASE_URL`, `COPYCAST__REFRESH__CONCURRENCY`,
`COPYCAST__AUTH__PASSWORD`, ...

`[engine.options]` takes raw yt-dlp options (for example `cookiefile`, `proxy`,
`sleep_interval`). Options Copycast owns (output paths, format selection, postprocessors,
hooks, ...) are rejected at startup with exit code 2; `cookiefile`, `proxy` and
`geo_verification_proxy` are accepted here only, never per Mirror.

### Environment-only variables

| Variable | Default | Meaning |
|---|---|---|
| `COPYCAST_CONFIG` | `./config/copycast.toml` (`/config/copycast.toml` in the image) | TOML file to read |
| `COPYCAST_BIND` / `COPYCAST_PORT` | `0.0.0.0` / `8080` | api listen address and port |
| `COPYCAST_WORKER_PORT` | `8081` | worker health endpoint port |
| `COPYCAST_WEB_DIR` | unset (`/app/web` in the image) | directory of the built web UI; unset disables the UI |
| `COPYCAST_LOG_FORMAT` | `console` (`json` in the image) | `console` or `json` |
| `COPYCAST_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `COPYCAST_AUTO_MIGRATE` | `true` | apply migrations at startup; when `false` the process waits up to 5 minutes for the schema head, then exits 2 |
| `COPYCAST__STORAGE__SCRATCH_DIR` | `./scratch` (`/scratch` in the image) | Scratch directory (also settable in the TOML) |
| `COPYCAST_TEST_DATABASE_URL` | unset | tests: use this Postgres instead of testcontainers |
| `COPYCAST_TEST_S3_URL` (+ `_ENDPOINT`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`) | unset | tests: run every test against this bucket (a prefix each) instead of `memory://` |
| `COPYCAST_TEST_NETWORK` | unset | tests: `1` enables the network-marked tests |

Compose-only (interpolated by `docker compose`, see `.env.example`): `COMPOSE_PROFILES`,
`TS_AUTHKEY`, `POSTGRES_PASSWORD`, `COPYCAST_BASE_URL`, `COPYCAST_AUTH_PASSWORD`,
`COPYCAST_AUTH_USERNAME`, `COPYCAST_IMAGE`, `COPYCAST_PORT`, `COPYCAST_UID`, `COPYCAST_GID`,
`COPYCAST_S3_SECRET_KEY`, `COPYCAST_S3_ACCESS_KEY`, `COPYCAST_S3_BUCKET`, `COPYCAST_S3_DATA`,
`COPYCAST_S3_VOLUME_MAX` and `COPYCAST_S3_VOLUME_SIZE_MB` (SeaweedFS: 64 volume files of
1024 MiB by default; they grow as written, so these only cap the store and the disk stays the
limit. Never `0`, "auto": SeaweedFS then fixes the count from the free disk at the first
start, and writes stop once those volumes are full even after space is freed).

## Authentication

Off unless `COPYCAST__AUTH__PASSWORD` is set (`COPYCAST_AUTH_PASSWORD` in the compose `.env`).
Basic auth is only a gate over TLS: with the `direct` profile put a TLS-terminating proxy in
front. The decision and its trade-offs are
[ADR 0011](adr/0011-optional-authentication-for-direct-deployments.md). Once on:

| Surface | Credential | Notes |
|---|---|---|
| Web UI, `/api/*`, `/api/events` | the operator pair (HTTP Basic) | `auth.username` defaults to `copycast`; the browser prompts once |
| `/feeds/<id>.xml`, media, assets | that feed's own pair, or the operator pair | one pair opens one feed; a wrong pair is 401 with a `Basic` challenge |
| `/mcp` | an API key as `Authorization: Bearer cck_…` | keys only; the operator pair is refused |
| `/healthz/*` | none | probes and healthchecks stay open |

**Feed credentials.** Every feed has a random username (8 characters) and password (24) minted
at creation; migration 0002 mints them for feeds that existed before. They live in Postgres
and in `feed.json`, so a rebuild keeps every subscription working. The UI shows them under the
feed URL, which already carries them as `https://user:pass@host/feeds/<id>.xml`. *Rotate*
(`POST /api/feeds/{id}/credentials/rotate`) mints a new pair and cuts off every client holding
the old one.

**Media keys.** A player streaming an enclosure (iOS AVPlayer in Overcast) sends no
credentials and never answers a challenge, so while authentication is on a feed publishes its
media, chapters, transcripts and attachments under `/feeds/<id>/k/<key>/…`: the key is derived
from the feed's password, opens only that feed's files, rotates with the pair and is logged as
`***`. A reverse proxy's own access log records it with the path. Bare `/feeds/<id>/media/…`
URLs still work with the pair. The upgrade to 2.0.2 moves every enclosure URL once, so apps
may download Episodes they already have again (ADR 0020).

**API keys.** Minted and revoked on the *API keys* page (`/api/keys`), shown once, stored as a
SHA-256 digest, with a scope: `read` (read-only tools), `write` (everything but deletions) or
`full` (everything, including `delete_feed`, `delete_item`, `prune_bucket`, and setting a
Bucket's Retention through `create_bucket` or `update_bucket`, since autoprune deletes). Every tool
checks the scope before running and refuses with a `ToolError` naming the scope it needs.
`last_used_at` is updated at most once a minute per key. Keys cannot mint keys: the key
capabilities have no MCP tools. For Claude Code:

```bash
export COPYCAST_MCP_KEY=cck_...
claude mcp add --transport http copycast https://copycast.example/mcp --header "Authorization: Bearer $COPYCAST_MCP_KEY"
```

or in `.mcp.json`:

```json
{
  "mcpServers": {
    "copycast": {
      "type": "http",
      "url": "https://copycast.example/mcp",
      "headers": { "Authorization": "Bearer ${COPYCAST_MCP_KEY}" }
    }
  }
}
```

Claude Desktop and claude.ai custom connectors accept OAuth only and cannot use a key.

**Turning it on later** breaks every existing subscription until each podcast app is given
its feed's pair; agents need a key. Turning it off makes everything open again; the pairs
and keys are kept for the next time.

Kubernetes: the example overlay's `api-auth.yaml` reads the password from a Secret named
`copycast-auth` (key `password`) and stays valid while the Secret is absent.

### The Object store

```
LAYOUT_VERSION                     "2"; written on first start into an empty bucket, a mismatch refuses to start
engine/cookies.txt                 the engine's cookie jar, merged from the files below
engine/cookies/<name>.txt          the stored cookie files, one per upload (optional)
engine/defaults.json               the Settings-page defaults
feeds/<feed_id>/feed.json          descriptor of the feed, its policy, items, assets and requests
feeds/<feed_id>/source/            verbatim Source XML or the last engine listing
feeds/<feed_id>/media/             audio, <item_id>.info.json, <item_id>.item.xml (original RSS item)
feeds/<feed_id>/assets/            Artwork, chapters, transcripts, Attachments
uploads/<upload_id>/               a staged Upload (upload.json, file.<ext>), swept after 24 h
```

The descriptor is rewritten after every change of intent and is what `copycast rebuild`
reads. Only the worker writes into `media/`, and only finished files: partial downloads and
transcodes live in Scratch (`<scratch_dir>/jobs/<item>.<random>/`), which a job removes when
it ends and a starting worker sweeps. Keep the bucket private: Copycast proxies every byte it
serves (feed credentials and download counting stay in front), so no podcast app ever talks
to S3. Configure the server to abort incomplete multipart uploads after a day.

How a first play is served (Ephemeral Mirrors): the request queues the archive on the
worker's reserved on-demand slot (never behind a Backfill), holds the line without sending a
byte, is woken by the item's `archived` event, and then serves the finished object, with every
tag and the artwork in place. A request still waiting after 50 s answers `503 Retry-After:
30` before iOS gives up on its side (it drops a silent request after 60 s); the archive keeps
running and the next tap is served at once.

### Moving to the Object store (from 1.x)

A 1.x data directory is copied once into the Object store. The directory itself is left untouched
and stays the rollback until you delete it:

```bash
docker compose stop api worker
docker compose up -d seaweedfs                     # the new stack's Object store
docker compose run --rm -v "$PWD/data:/legacy:ro" worker storage push --from /legacy --dry-run
docker compose run --rm -v "$PWD/data:/legacy:ro" worker storage push --from /legacy
docker compose up -d
```

`storage push` uploads every file under `engine/` and `feeds/` to the key of the same name
(partials and `tmp/` stay behind). It skips objects already present with the same size, so
an interrupted push resumes, and it checks every upload. It writes `LAYOUT_VERSION` 2 last,
and only when every file has landed; until then both processes refuse to start on the Object store.
It exits 1 when a file failed: run it again. To roll back, start the previous image on the
untouched `./data` directory.

## Mirror modes

A Mirror's Settings tab and the Settings page offer two policies as tabs, Ephemeral and
Archive; Archive's *Backfill* switch picks between its two modes:

| Policy (mode) | Archives | Deletes |
|---|---|---|
| Archive, Backfill on (`all`) | every listed item, then whatever Follow brings | never |
| Archive, Backfill off (`new`) | only what the Source lists after the policy is applied | never |
| Ephemeral (`automatic`) | nothing until a podcast app asks for an Episode's media; the request waits up to two minutes, then answers `503 Retry-After: 30` while the download continues | Episodes idle for *Retention* days after their last download (7 by default; empty keeps forever), checked by the worker's prune pass |
| Rolling N (`rolling`) | the newest N by publication, at every Refresh | archived items outside the window, at the next Refresh and at once when you switch to it |
| Selection (`selection`) | exactly the numbers given | never |

Rolling N and Selection are no longer offered as tabs: a Mirror that uses one keeps a third
tab for it until switched, and agents still pass them to `create_mirror` and `update_mirror`.
Choosing Archive starts with Backfill off; switching to Archive without Backfill starts from
that moment: nothing listed so far is archived. New Mirrors start from the operator's default policy (the Settings page; Ephemeral
with a 7-day retention out of the box), both in the wizard and for an MCP `create_mirror`
without `backfill`.

An Episode flagged **Archive** (the archive button on its Catalog row, `PUT
/api/feeds/{id}/items/{item}/archive-flag`, the `set_archive_flag` tool) is kept for good:
Ephemeral and Bucket Retention, a Rolling window and Sync skip it. The admin purge does not.

Switching modes previews the change (`POST /api/mirrors/{id}/preview`) and asks for
confirmation when archived Episodes would be deleted; rolled-out and expired Episodes leave
Tombstones and can be archived again on purpose. An Automatic feed keeps listing expired and
deleted Episodes and downloads them again when a podcast app asks. The Mirror header menu
offers *Archive all Available…* (queues everything not archived yet, Shorts and Tombstones
included, with the option to switch the Mirror to Everything) and *Retry N failed*; the same
actions are the `archive_available` and `retry_failed` tools and routes. Over MCP, `create_mirror` and
`update_mirror` refuse Rolling and expiring Automatic modes without a `full` key. Latest N
stays on Mirrors that already use it but is no longer offered ([ADR 0012](adr/0012-mirror-modes-rolling-and-automatic.md)).

An Automatic Mirror's feed lists every item the Source lists, archived or not, and an item
keeps one media URL for its whole life: `/feeds/{id}/media/{item}.{public_ext}`, where
`public_ext` is fixed when the item is first listed (`m4a` for an Engine Source; for an RSS
Source the container the Engine will leave for the enclosure's MIME type or URL extension:
`mp3` and `m4a` as they are, MPEG-4 video as `m4a`, everything else as `mp3`; `mp3` when
nothing is known) and never rewritten,
whatever container the archive produces. Podcast apps treat a changed enclosure URL as a new
file and download it again on every device, which is what 1.2 caused by advertising `.mp3`
before the archive and `.m4a` after it. Until the item is archived the enclosure's `type`
follows `public_ext` and its `length` is estimated from the duration at 128 kbit/s (apps
refuse 0-byte enclosures); once archived it carries the real MIME type and size. The media
route serves the file under that URL, under the archived file's own extension and, for an
Automatic feed only, under the `.mp3` placeholder URL 1.2 advertised, so apps that still hold
it keep working. `public_ext` is written to `feed.json` and survives a rebuild; migration
0010 sets it for rows that existed before.

**Live streams.** A YouTube stream is archived once its recording is published, never while
it is on air. Every listing records what the Engine says the item is doing (`live_status`:
upcoming, live, recording being processed, recorded); a stream that is upcoming or live is a
Catalog item like any other, shown with an *Upcoming* or *Live* badge next to its state, but it
is not archivable: no Backfill, Follow or selection wants it, `archive_item` refuses it
(`422 source-unsupported`), and an Automatic feed does not list it. The next Refresh, full or
light (so the very next fetch of the feed, once the cooldown has passed), sees the stream
recorded, makes it archivable, lists it in an Automatic feed and archives it when Follow or
the Backfill wants it. An archive job that nevertheless meets a stream with no recording yet
(still live, not started, or ended but still being processed by YouTube) downloads nothing:
the job is queued again 30 minutes later (at the scheduled start for an upcoming stream, when
that is later, but never past the job's 48 h ceiling) without counting an attempt, the item
goes back to *Queued* with the reason as its last error and the badge explaining why, and this
repeats for up to 48 h after the job was created; then the job and the item fail for good
("still not published after 48 h") and *Retry N failed* starts the wait over. A stream pushed
into a Bucket as a Request is wanted at once whatever its status, because a Bucket has no
Refresh that would notice the recording later: the row shows *Queued* with the *Live* or
*Upcoming* badge and its archive job waits as above (`add_to_bucket` of a stream on air archives
the recording once YouTube publishes it). YouTube lists nothing for a stream or premiere that
has not started, so a Request for one alone fails at expansion with YouTube's own reason ("This
live event will begin in 2 hours"): push it again once it is live or recorded; a playlist
Request that contains one gets a waiting item like any other. RSS Sources are untouched.
Nothing is repaired: an Episode archived from a stream while it was live (before 1.3.1) keeps
the partial recording; delete it and archive it again once the recording is published
([ADR 0014](adr/0014-live-streams-archived-once-recorded.md)).

## YouTube dates and descriptions

A flat channel listing carries no upload date and no description, which left every video
undated (and unordered in Overcast) until it was archived. Listings now ask yt-dlp for an
approximate date (``youtubetab:approximate_date``, from the "3 weeks ago" text) and merge the
channel's Atom feed (``/feeds/videos.xml?channel_id=…``, one request, no cookies), which
carries the exact date and the full description of the fifteen newest videos. A Refresh fills
what a row still lacks; an approximate date never replaces a stored one (it would drift), an
exact one does, and archiving fills in the download's own date. While a date is approximate the
Catalog shows it as "≈ 3 weeks ago" and the feed's show notes open with "Published about 3
weeks ago (approximate date from YouTube; exact once downloaded)".

## Light Refresh on fetch, directory links and playlists

- **Light Refresh on fetch.** A `GET` of a Mirror Feed first checks the Source's newest items
  inline, in the api, so they are in that very answer; nothing is queued for the worker and
  nothing waits on it. What is checked depends on the Source: an RSS Source gets one
  conditional GET (`ETag`/`Last-Modified`; a 304 changes nothing, a 200 is a complete listing
  that may Delist), a YouTube channel or any other Engine Source a shallow listing of its first
  fifteen entries that adds and re-dates items but never Delists or renumbers, and a YouTube
  playlist (listed oldest first, so a first page is not its newest items) queues the normal
  full Refresh instead and answers at once. After a listing the policy runs as in a Refresh and
  archive jobs are queued for what it wants; the feed's ETag moves only when something changed.
  The check is skipped while the Mirror is Paused and within the cooldown
  (`refresh.fetch_cooldown_minutes`, 5 by default) after any Refresh, full or light; it is
  capped at 8 s, after which the feed is served as it is and the run is recorded as failed. A
  Source error never degrades the feed either. `HEAD` requests and Bucket feeds never check.
  The Mirror header shows *Checked on fetch*; the runs are `refresh_runs` rows flagged `light`
  and never touch the Mirror's health, `last_error` or its last-Refresh stamps. Only a full
  Refresh (scheduled or *Refresh now*) Delists a channel's items.
- **Apple Podcasts and Spotify links.** Pasting a `podcasts.apple.com` link resolves the feed
  through Apple's lookup API; an `open.spotify.com/show/…` link is resolved by the show's name
  through Apple's directory (an exact match is used, otherwise every hit is a candidate to pick
  from). `probe_source` and `create_mirror` accept both, so agents can too.
- **Playlists kept in sync.** A Mirror with *Stay in sync with the Source* deletes an Episode
  when its item leaves the Source instead of Delisting it (a Tombstone, so the video comes back
  as Available if re-added). The *Capture a YouTube playlist* dropdown of the Buckets lists the
  signed-in account's playlists through the engine when it opens (`GET /api/youtube/playlists`,
  the cookie file required, Watch Later first) and captures any of them with a click: a synced
  Mirror in Automatic mode flagged `playlist_capture`, which is listed among the Buckets rather
  than with the Mirrors. Agents get the same through `list_youtube_playlists` and `create_mirror`.
- **The Feeds page** (`/`) lists every Feed: the Mirrors table first, then the Buckets and the
  captured playlists. `/mirrors` and `/buckets` redirect there, and so do the `/inboxes` links
  from before Inboxes were renamed Buckets (ADR 0018).
- **Uploading files into a Bucket**: *Upload files…* on a Bucket's page takes mp3, m4a and m4b
  files, sends them one by one in natural file-name order (Part 2 before Part 10) with a
  progress bar each, then adds them as archived Episodes dated by arrival, one second apart
  (`create_upload`, the `PUT` to its `upload_url`, then `add_uploads`). Agents use the same
  three steps; `create_serial_bucket` stays the way to publish a work in an order they choose.
- **Adding a Source** happens in a dialog from the Feeds page bar (a URL, an Apple Podcasts or
  Spotify link, or a name to search) or the command palette; the Mirror takes the operator's
  default policy, changed afterwards in its Settings tab.

## Show notes, OPML and the player

- **Attachments.** When an Episode is archived, the images its show notes embed and the
  files they link to (images, PDFs, audio; up to 20 per Episode, 5 MiB per image, 20 MiB per
  file) are mirrored as `attachment` assets under `assets/{item}.attachment.{slot}.{ext}`,
  and the Mirror Feed's description points at the local copies. A file that cannot be fetched
  is recorded as a failed asset and its original URL stays in the notes.
- **OPML.** *Export OPML* on the About page (`GET /api/feeds.opml`) lists every feed with
  its credentialed URL, to subscribe to all of them in one go.
- **Player.** The web player has a seek bar with chapter marks and the time left, a chapter
  list under the current chapter's title (from the Episode's chapters asset), skip back 15 s /
  forward 30 s, and volume and mute (no volume slider on iPhone and iPad, where the hardware
  buttons own it).
  - *Speed* goes from 0.5× to 3× in steps of 0.05: the speed chip opens a slider with −/+ and
    presets, and scrolling over the chip or the slider (mouse wheel or trackpad) changes it.
  - *Resume.* Each browser remembers where you were in its last 500 Episodes and starts there
    again, a few seconds earlier after a pause of a minute (3 s), an hour (10 s) or a day
    (20 s); an Episode starts over when it is Finished (played to the end, or stopped in its
    last 30 s), barely started or nearly over. After a reload the bar comes back paused.
  - *Sleep timer* (the moon): pause after 5–60 minutes, with a 10 s fade except on iOS, at the
    end of the chapter, or at the end of the Episode.
  - *Lock screen and headphones* (Media Session): play/pause, skip, scrubbing, artwork, and
    chapter previous/next outside iOS.
  - *Keyboard*: Space, ← / → (skip), Shift+← / → (chapters), `[` / `]` (speed ±0.1×), `m`
    (mute), ↑ / ↓ (volume, only while the player has focus). They leave keys alone where the
    focused control uses them, and *Single-key shortcuts* in the `?` dialog turns them off.
    Speed, volume and that switch are remembered per browser.
- **Title.** The pen next to a Mirror's title edits it in place; Enter saves, Escape cancels.
- **Descriptions on demand.** Expanding a Catalog row without a description asks the Source for
  it (and the exact date and artwork) right there; `fetch_item_metadata` does the same for
  agents.

## Titles, artwork and crawlers

- A Mirror's Settings tab has a *Title* field: a title of your own is shown in the UI, the
  Mirror Feed and MCP and survives Refreshes (`title_override`; the Source's title stays in
  `source_title`); *Use Source title* clears it.
- Artwork assets (`/feeds/{id}/assets/*.artwork.*`) and the Copycast logo
  (`/feeds/copycast-artwork.png`, the artwork of a Bucket feed without its own) answer without
  credentials even while authentication is on: podcast apps fetch images bare, and the URLs
  stay unguessable. Media, feeds, chapters and transcripts keep the feed's pair.
- `/robots.txt` disallows everything and every response carries
  `X-Robots-Tag: noindex, nofollow, noarchive`; the UI shell adds the equivalent meta tag.
- The About page's trash button next to the totals deletes every archived Episode of every
  feed after a dry run (`POST /api/admin/purge`, `dry_run` true by default); feeds, Catalogs,
  artwork and credentials stay, and the Episodes leave Tombstones.

## Settings page: Authentications, Mirrors and Buckets

The Settings page saves every change as it is made, with no Save button. *Authentications*
shows YouTube, Twitch, Vimeo and Mixcloud with a green check once a stored cookie file covers
them (see below). *Buckets* lists every Feed's storage, the Mirrors in the Feeds page's order,
then the Buckets and captured playlists, each with its size and a trash button. A bucket
holding Episodes flagged Archive warns first and offers *Delete the rest* (`POST
/api/feeds/{id}/clear`, the `clear_feed` tool: a dry run by default, it deletes every archived
Episode not kept for good and keeps the feed) next to *Delete everything*. Every Episode of a
Mirror on the Archive policy counts as kept. The default Bucket can only be emptied.

The *Mirrors* half holds defaults that live in the Object store (`engine/defaults.json`,
`PUT /api/settings/defaults`) rather than in the environment, because they are policy the
operator changes from the UI:

| Default | Effect | Per-Mirror override |
|---|---|---|
| Metadata language (`fr`, `pt-BR`) | yt-dlp asks YouTube for that language instead of English, so a French channel's titles and descriptions stay French | the Mirror's *Metadata language* field (`preferred_language`; `language` stays what the Source reports) |
| Minimum length (minutes) | items shorter than that are listed but never archived by Backfill or Follow; unknown lengths pass; explicit selections still work (keeps Shorts out) | the Mirror's *Minimum length* field (`min_duration_seconds`) |
| Policy | the policy a new Mirror starts from: Ephemeral (with its retention) or Archive (with or without Backfill) | the Mirror's Settings tab, or `backfill` in `create_mirror` |
| Refresh every (hours) | how often the scheduler Refreshes a Mirror (24 by default; replaces the `refresh.interval_hours` config key) | the Mirror's *Refresh every* field (`refresh_interval_hours`) |

A Mirror's Settings tab shows "Using the default" when a field is empty, marks a value that
diverges from the default, and offers *Use default*, which clears the override (sent as
`null`). Changing a default applies from each Mirror's next Refresh. Both capabilities have
routes but no MCP tools.

Engine-sourced Episodes (YouTube, SoundCloud, Bucket Requests) carry the Source's page URL at
the end of their description, since most podcast apps never show the item's `<link>`.

`search_videos` (`GET /api/search/videos`, an MCP tool, and the wizard's search step) finds
videos on YouTube through the engine; an agent asked for "the last WWDC live from Gruber"
searches, picks the newest hit and pushes its URL with `add_to_bucket`.

## YouTube and other sites that need a login

YouTube answers requests from a server's address (a Linode or Hetzner IP, say) with
"Sign in to confirm you're not a bot", and the item fails permanently. The fix yt-dlp
recommends is the cookies of a logged-in browser session:

1. In a browser, open a private window and sign in to youtube.com.
2. Export its cookies with a "Get cookies.txt LOCALLY" extension (Netscape format).
3. Close the private window without signing out, so YouTube does not rotate the session.
4. Add or paste the file under *Authentications* on the **Settings** page (`PUT
   /api/engine/cookies` with the file's `name`); the site's tile turns green.
5. **Retry** the failed Episodes from the Catalog.

The image also ships `deno`, the JavaScript runtime yt-dlp needs for YouTube's signature
and "n" challenges; without it YouTube extraction degrades to a warning-laden fallback that
is refused far more often. Each file is stored in the Object store at
`engine/cookies/<name>.txt` and never shown again; a file with the same name replaces it,
another name adds a row. The files are merged into `engine/cookies.txt`, handed to yt-dlp as
`cookiefile` on every listing and fetch, in both processes. Each call works on a private
Scratch copy (mode 0600) and the jar is replaced when the site rotated a cookie, so two jobs
never tear it; every later change to the files first carries the jar's rotated cookies back
into the file they came from. A jar stored before cookie files existed shows as the file
`cookies`. An explicit `cookiefile` under `[engine.options]` still wins. Remove a file from
the same page (`DELETE /api/engine/cookies?name=`) to drop its sites back to anonymous
fetches. An agent cannot
read or replace it: the three capabilities have routes but no MCP tools.

What lands in `media/` (after the Scratch work is done): mp3 and m4a enclosures and streams are copied byte for byte; AAC in
a video container is remuxed to m4a; Opus, Vorbis, FLAC, WAV and the like are transcoded
once to mp3 at 192 kbit/s, so every Episode plays in every podcast app and carries its
chapters. Cover art already embedded in a file is kept; the feed's episode image is then
stored as the Episode's Artwork asset next to it.

Thumbnails never fail an archive: a CDN that serves a JPEG under a `.png` URL is detected
from the bytes and renamed before ffmpeg converts it, and an image ffmpeg still refuses only
costs the embedded artwork, with a warning in the job log.

## Kubernetes

`deploy/kubernetes/base` is a kustomize base; `deploy/kubernetes/overlays/example` shows what
to change. Prerequisites: the [CloudNativePG](https://cloudnative-pg.io) operator, the
[Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator) and an S3
bucket (SeaweedFS's Helm chart, a cloud bucket, ...).

```bash
cp -r deploy/kubernetes/overlays/example deploy/kubernetes/overlays/mine
$EDITOR deploy/kubernetes/overlays/mine/copycast.toml      # base_url, [storage] url and endpoint
kubectl -n copycast create secret generic copycast-storage \
  --from-literal=access_key_id=... --from-literal=secret_access_key=...
kubectl apply -k deploy/kubernetes/overlays/mine
kubectl -n copycast get pods,ingress
```

What the base creates in namespace `copycast`:

- `Cluster copycast-db` (CNPG, one instance, 10Gi). The operator writes the connection URI
  into secret `copycast-db-app` (key `uri`), which both Deployments read.
- `Deployment copycast-valkey` and `Service copycast-valkey`: the shared Feed cache (no
  persistence, LRU at 256 MiB).
- `Deployment copycast-api` (1 replica, RollingUpdate, migrates at startup; stateless, so
  raise `replicas` freely) and `Deployment copycast-worker` (1 replica, Recreate,
  `COPYCAST_AUTO_MIGRATE=false`: it waits up to 5 minutes for the api to migrate, then exits
  and is restarted; Scratch is a 20Gi `emptyDir`). Both read the Object store's keys from the
  Secret `copycast-storage`. Probes on `/healthz/ready` and `/healthz/live`.
- `Service copycast` (ClusterIP 8080) and `Ingress copycast` with `ingressClassName: tailscale`
  and host `copycast`, so the app is `https://copycast.<tailnet>.ts.net` inside your tailnet.

The worker is active-passive by construction (one runs at a time); the api scales out.
Nothing is pinned to a node: the archive lives in the Object store.

## Backup and restore

Back up the Object store (for SeaweedFS in compose, the `seaweedfs` volume or the
`COPYCAST_S3_DATA` directory while it is stopped, or any S3 sync tool against the bucket). It
is sufficient: media, sidecars, original Source XML, assets and the `feed.json` descriptors
reconstruct the database. Optionally add a `pg_dump` to keep
telemetry (job history, refresh runs, download counters, engine versions):

```bash
docker compose exec postgres pg_dump -U copycast -Fc copycast > copycast-$(date +%F).dump
```

Restore: put the Object store back, start Postgres, then either restore the dump
(`pg_restore -U copycast -d copycast copycast.dump`) or run a rebuild (below). Stop the
worker before touching objects under `feeds/*/media/`.

## Rebuilding the database from the Object store

`copycast rebuild` lists each feed's prefix once and reconstructs feeds, items, assets and
requests from `feed.json` and the sidecars, reconciles `media/` with the item rows (audio
present ⇒ archived; object without a row ⇒ a row from its `.info.json` / `.item.xml`), checks
the assets, recounts storage and recreates the default Bucket. It is idempotent and refuses
to run while the worker holds its
lock, so stop the worker first.

```bash
docker compose stop worker
docker compose run --rm worker rebuild          # add --yes to also delete rows absent from the Object store
docker compose start worker
```

Lost by a rebuild: jobs and their logs, refresh runs, heartbeats, engine version history and
download counters. Everything else, including item ids and ordinals, comes back identical.

## Engine bumps and rollback

The engine (yt-dlp) is pinned in `uv.lock`. `engine-bump.yml` runs every two weeks (the 1st
and 15th of the month, or on demand from the Actions tab), pins the newest release of the
configured channel (`vars.ENGINE_CHANNEL`, nightly by default), runs the engine tests, and
pushes the lock change to `main`; `image.yml` then builds and publishes:

| Tag | Moves? | Use |
|---|---|---|
| `latest` | yes | the newest successful build |
| `2.0.3` | yes, on every engine bump | "the current 2.0.3" |
| `2.0.3-yt2026.10.26.220202.dev0` | never (until pruned) | exactly this app + engine combination |
| `sha-<short>` | never | one commit |

Every image is smoke-tested (`scripts/smoke.sh`) before its tags move. After each build the
package is pruned to the three newest tagged images; older images and every untagged manifest
are deleted, so roll back within that window or pull and keep the image yourself. The prune
runs with `GITHUB_TOKEN`, which needs the package to grant the repository the Admin role
(Package settings → Manage Actions access). To roll back an engine that broke a site, pin
the previous immutable tag in `.env`
(`COPYCAST_IMAGE=ghcr.io/flrntdfr/copycast:2.0.3-yt<previous>`) or in the kustomize overlay
(`images[].newTag`), and `docker compose up -d` / `kubectl apply -k`. The About page and
`copycast --version` show which engine is running; `jobs.engine_version` records which engine
archived each item.

Locally: `make engine-version` prints the pinned version, `uv run python
scripts/engine_newest.py --channel stable` the newest published one, and
`uv add "yt-dlp[default]==<version>"` pins a specific one (the exact pin lives in pyproject.toml and uv.lock).

## Dependency pinning

Every dependency is pinned to an exact version so a rebuild months from now produces the same image:

| Where | How it is pinned | What moves it |
|---|---|---|
| `pyproject.toml` + `uv.lock` | `package==x.y.z` for every runtime and dev dependency; `requires-python = ">=3.13,<3.14"`; `[tool.uv] required-version` | Dependabot (`uv` ecosystem, daily); yt-dlp only through `engine-bump.yml` (`uv add "yt-dlp[default]==<version>"`) |
| `web/package.json` + `pnpm-lock.yaml` | exact versions, `save-exact=true` in `web/.npmrc`, `packageManager: pnpm@11.25.0` | Dependabot (`npm` ecosystem, daily) |
| `Dockerfile`, compose files, CI service containers, CNPG `imageName` | `image:tag@sha256:digest` | Dependabot (`docker` and `docker-compose` ecosystems, weekly); the `postgres` service in `ci.yml` and the CNPG image are bumped by hand |
| GitHub Actions | `owner/action@<commit sha> # vX.Y.Z`; `astral-sh/setup-uv` also pins `version: "0.11.21"` | Dependabot (`github-actions`, weekly); bump the uv version together with the `ghcr.io/astral-sh/uv` image and `[tool.uv] required-version` |
| Nix devShell | `flake.lock` | `nix flake update` when you want a newer nixpkgs |

To bump something by hand: `uv add "name==x.y.z"` (never edit the `==` pin without re-locking), `pnpm --dir web add name@x.y.z`, or edit the digest and run CI. `uv lock --upgrade-package name` cannot move an exact pin; that is intended.

## Logs and events

Both processes log structured lines (JSON in the image) with `process`, `request_id`
(echoed as `X-Request-ID`), `job_id`, `mirror_id` and `bucket_id` bound where applicable.
Job progress and state changes are also streamed to the UI over `GET /api/events` (SSE) and
stored per job in `job_log_lines` for the Jobs page.

The api writes its own request log (uvicorn's access log is off): one `http.request` line
per request, emitted once the last body byte went out, with `method`, `path`, `query` (null
when empty), `status`, `duration_ms`, `bytes` (body bytes sent, after compression),
`content_length` (the response header, when set), `client_ip` (the first hop of
`X-Forwarded-For`, else the socket peer), `forwarded_for` (the raw header), `user_agent`,
`range`, `request_id` and `completed` (false when the response never finished: an unhandled
exception is logged as `status=500`, a client that went away with the status it got).
Requests under `/healthz/` are never logged; `Authorization` and usernames never appear. A
fetch that ran a Light Refresh also logs one `light_refresh.done` line (`feed_id`, `status`,
`kind`, `new`, `wanted`, `enqueued`, `duration_ms`). A media key in a path is logged as `***`.

Feeds are rendered in a thread and gzipped once per revision when they enter the Feed
cache; a client that sends `Accept-Encoding: gzip` gets those bytes as they are
(`Content-Encoding: gzip`, `Vary: Accept-Encoding`), so a cache hit costs no rendering and no
compression, however large the feed.

Every request of the api runs on one event loop, so anything that blocks it delays all of
them. A watchdog thread logs `api.event_loop_blocked` (warning) once the loop has not ticked
for a second, with `blocked_ms`, the `request_path` being served (keys redacted) and the
loop's `loop_stack` at that moment, innermost frame last; `api.event_loop_stalled` follows with
`stalled_ms` when the loop is back. A healthy api logs neither: each one is a bug to report
with its stack.
