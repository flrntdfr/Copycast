# Copycast

A self-hosted podcast mirroring and archiving service. You point it at a Source of audio content (a podcast feed, a page advertising feeds, a YouTube channel or playlist, anything the Engine supports); it keeps a durable copy in its Object store and publishes its own podcast feed that keeps working even if the original disappears. Agents get the same capabilities as the web UI through an MCP server: find a podcast by name and mirror it, archive an exact selection of episodes, or push arbitrary URLs into a Local feed.

Copycast v1 is a Python (FastAPI, SQLAlchemy, Postgres) modular monolith with yt-dlp imported as a library in a worker process, a React frontend, and an MCP server sharing the API's service layer. It ships as one container image run as two processes, next to Postgres, an S3 Object store
(SeaweedFS in compose) and an optional Valkey Feed cache.

## Language

Use these terms, in code and in conversation. The _Avoid_ lists name the words that mean something else here or that blur a distinction.

**Feed**:
Everything Copycast publishes is a Feed: a Mirror or a Local feed. Every Feed has an id, a title, an Owner, a Catalog, a published RSS document and exactly one bucket (its files under `feeds/{id}/` in the Object store). The Feeds page lists every Feed the caller can see, Mirrors, captured playlists and Local feeds alike, in one table.
_Avoid_: Channel, show, podcast (for the thing Copycast serves)

**Mirror**:
A Feed that tracks one Source. Created once, then kept up to date automatically; it continues serving its archived content even when the Source becomes unreachable. A captured YouTube playlist is a Mirror too.
_Avoid_: Archive, snapshot, copy, subscription

**Source**:
The external URL a Mirror tracks — a podcast RSS feed, a YouTube channel or playlist, or anything else the Engine supports. Compared for duplicates through a normalized dedup key; fetched with the URL exactly as given.
_Avoid_: Original, upstream, target

**Mirror Feed**:
The RSS feed Copycast publishes for a Mirror — the thing you subscribe to in a podcast client. It preserves the Source's original metadata but points at archived audio and assets.
_Avoid_: Output feed, generated feed, proxy feed

**Local feed**:
A Feed with no Source (`kind: "bucket"` in the API and MCP, which keep the older name). It is filled by Requests — arbitrary URLs pushed by a person or an agent — and by Uploads, and its page holds the *Add URL* form, *Upload files…* and its Settings (name, autoprune). Every User has one default Local feed named "Copycast", created at startup: it can be renamed but never deleted, and the UI creates no other (agents can, with `create_bucket`). Called an Inbox before ADR 0018 and a Bucket before ADR 0021; `feed.json` files that still say `inbox` read as `bucket`.
_Avoid_: Bucket (that is a Feed's files now), Inbox, queue, dropbox, watch later

**Local feed's RSS**:
The RSS document Copycast publishes for a Local feed: the same shape as a Mirror Feed, with every item synthesized from Engine metadata and ordered by the time it was added.
_Avoid_: Bucket Feed (the old name), request feed

**Serial Bucket**:
A Local feed an agent builds from Uploads in an order it decides (an audiobook's parts and chapters): Episodes archived at creation, numbered by position, dated one per day at a playful posting time so podcast apps play them first to last, and published as `itunes:type serial` (ADR 0015). The tool keeps its name, `create_serial_bucket`.
_Avoid_: playlist, audiobook feed, ordered Bucket

**Upload**:
One audio file staged for a Local feed: created with a one-time upload URL, received by an HTTP PUT of its bytes, read for its tags, and consumed (or swept after 24 h, or given up with `delete_upload`) once a Serial Bucket or `add_uploads` takes it. Each Owner's staged uploads are counted in `staged_uploads` (ADR 0029). `add_uploads` orders a batch by natural file name and dates it by arrival (ADR 0018).
_Avoid_: Import, attachment (that is a show-notes file), file request

**Request**:
One URL pushed into a Local feed, recorded with who pushed it (UI or MCP) and what it expanded into. A Request for a page or playlist expands to many Catalog items; a Request for a podcast feed is refused ("use create_mirror").
_Avoid_: Submission, task, job (that is the worker's word)

**Episode**:
A Catalog item with archived media; its Mirror Feed entry is captured once at archive time and never rewritten from the Source afterwards. The Mirror Feed lists every Episode ever archived that has not been deleted by you.
_Avoid_: Item (say Catalog item when it may not be archived), entry, track, video

**Artwork**:
The cover image of a Feed and the images of its Episodes, archived alongside the audio. Published in every feed and in `AssetRead.url` at one public URL, `/art/{asset_id}-{mac}.{ext}`, whose MAC under the instance secret (32 random bytes in Postgres) is its only credential, in both auth modes (ADR 0022); its bare path under the Feed takes the Feed credentials like any other asset.
_Avoid_: Thumbnail, image, artwork key, signed URL

**Catalog**:
Everything a Feed's detail page shows: every item the Source advertises (whether archived or not) plus anything archived that the Source has since dropped. Each Catalog item shows one state: Listed, Delisted, Available, Queued, Archiving or Failed.
_Avoid_: Backlog, list, index

**Catalog item**:
One row of a Catalog: a thing the Source lists or once listed, identified by its Source key (RSS guid, else enclosure URL; extractor plus id for the Engine) and carrying an Ordinal. It becomes an Episode when its media is archived.
_Avoid_: Row, record, entry, video

**Available**:
The state of a Catalog item the Source advertises but that has no archived media, including one you deleted. It is shown with its Source metadata and can be archived on demand; until then it is absent from the published feed.
_Avoid_: Pending, unarchived, missing

**Delisted**:
The state of an Episode that the Source no longer lists. A Delisted Episode remains in the Mirror Feed unchanged; its state is visible only in the Copycast UI.
_Avoid_: Removed, deleted, orphaned

**Ordinal**:
A Catalog item's permanent position in its Feed, counted from the oldest item, assigned once when the item is first seen and never renumbered. Ordinals are the fallback numbering for "Episode N" when the Source does not number its items.
_Avoid_: Index, number (say Source number or Ordinal), position

**Source numbering**:
The episode number a Source publishes itself (`itunes:episode`, a playlist index). When it exists and is unique it wins over the Ordinal for display and for agent requests such as "1-42, 180".
_Avoid_: Episode id, track number

**Mirror ID**:
A Feed's permanent, opaque identity. For a Mirror it is derived from the Source's dedup key at creation (in users mode salted with the Owner, `owned_mirror_id`, so it tells nobody what another User mirrors; ADR 0028); for a Local feed from its name plus a random suffix. It names the feed URL and the Feed's prefix in the Object store and never changes — podcast clients depend on it.
_Avoid_: Slug, name, feed name

**Backfill**:
The part of a Mirror's policy that says which items get archived (`backfill.mode`): Archive with or without its backfill (`all`, `new`), Ephemeral (`automatic`), and for agents and the Mirrors already using them a Rolling window or an explicit selection (Latest N remains too). Everything and Latest N apply once, at the first Refresh (immediately for a selection); `new` wants only what is first seen after it was applied. On the Archive tab, *Backfill* is the switch that also archives everything already published; it starts off when Archive is chosen.
_Avoid_: Initial sync, history, catch-up, mode (in prose; the UI shows the policies as tabs)

**Archive**:
The policy that keeps whatever it archives for good: with Backfill on (`all`) every item listed, then every new one; with it off (`new`) only what the Source lists from the moment it is chosen. Every Episode it archives is Locked automatically, and switching a Mirror to Archive Locks the Episodes it already holds, except on a Mirror with Sync, which is never Locked automatically (Sync would skip Locked Episodes); nothing else protects them (ADR 0021).
_Avoid_: Everything (the old tab name), permanent, keep

**Rolling**:
The Backfill mode that keeps exactly the newest N items by publication archived: applied at every Refresh and at once when chosen; archived items that fall outside the window are tombstoned, Locked Episodes excepted (ADR 0012). No longer offered as a tab: a Mirror on it keeps the tab until switched; agents may still choose it.
_Avoid_: Sliding window, keep-last, FIFO

**Ephemeral**:
The policy that archives nothing ahead of time (`automatic` in the API): the Mirror Feed lists every item the Source lists, an Episode is archived when a podcast app first asks for its media (the request waits up to two minutes, then says retry), and it expires after the Mirror's Retention. An item's media URL is fixed when it is first listed (`public_ext`) and never changes once archived, whatever container the archive got, so no app downloads an Episode twice.
_Avoid_: Automatic (the old name, still the API value), lazy, on-demand mode (say "archived on demand" for the act), streaming

**Lock**:
An Episode's mark that it is never deleted (`archive_flag` in the API, the database and `feed.json`; `set_archive_flag`), toggled with the padlock of its Catalog row or of any of its files on the bucket page. Retention, a Rolling window, Sync, purging the bucket, the admin purge and `delete_item` all spare a Locked Episode (the UI disables its Delete button); only deleting the whole Feed removes it. The Archive policy Locks what it archives unless the Mirror has Sync (ADR 0021).
_Avoid_: Archive flag (the old name), pin, star, favourite, protected, keep

**Follow**:
The part of a Mirror's policy that says whether items the Source lists after creation are archived automatically. Off by default when the Backfill is a selection.
_Avoid_: Auto-download, subscribe, track (that is what a Mirror does with a Source)

**Refresh**:
The recurring act of listing a Source in full, adding, re-dating and Delisting Catalog items, and archiving what the policy wants. Run by the worker on a schedule or on a manual action; a fetch of the Mirror Feed runs a Light Refresh instead (a playlist Source queues a full Refresh from it) and serving the feed never waits on the worker. A failed Refresh never degrades the Mirror Feed; the Mirror keeps serving what it has.
_Avoid_: Sync, update, poll

**Light Refresh**:
A fetch-triggered check of a Source's newest items, run inline by the api while it answers a `GET` of a Mirror Feed: an RSS Source gets one conditional GET (a complete listing when it changed), a channel a shallow first-page listing that adds and re-dates items but never Delists or renumbers, a playlist a queued full Refresh. Skipped while Paused or within the cooldown of any Refresh, capped at 8 s, never a wait on the worker; it archives what the policy wants like a Refresh, stamps `last_light_refresh_at` and leaves the Mirror's health and last-Refresh stamps alone.
_Avoid_: Quick refresh, pull to refresh, poll, sync

**Paused**:
The state of a Mirror that no longer Refreshes but keeps serving its Mirror Feed and archived Episodes. The only way to stop a Mirror without destroying its archive.
_Avoid_: Disabled, stopped, archived

**Retention**:
A rule for deleting Episodes no longer needed: for a Local feed, N days after their first download (*autoprune*); for an Ephemeral Mirror, the days after an Episode's last download before it is tombstoned (7 by default, none keeps forever). Other Mirrors have no Retention. Local-feed Episodes never downloaded, and Locked Episodes, are never pruned.
_Avoid_: Expiry, TTL, cleanup, garbage collection

**Download count**:
How many times an Episode's media was fetched by a client: a full GET or a Range request starting at byte 0 counts once; HEAD requests and later ranges never count. Drives Retention and is telemetry, not intent.
_Avoid_: Plays, listens, hits

**Tombstone**:
The trace left by an Episode you deleted (or that rolled out, expired, or was purged): the Catalog item stays, marked deleted, so the Mirror will not archive it again on its own; it shows as Available and can be re-archived on demand, and an Ephemeral Mirror downloads it again when a podcast app asks.

**Capture**:
A YouTube playlist of the signed-in account mirrored from the *Playlists* dropdown next to the *Mirror* button on the Feeds page: a synced, Ephemeral Mirror flagged `playlist_capture`, listed in the Feeds table like any other Feed.
_Avoid_: Playlist Bucket (it is a Mirror underneath), import

**Sync**:
A Mirror option (*Stay in sync with the Source*): an Episode is deleted, leaving a Tombstone, when its item leaves the Source, instead of staying Delisted. Meant for a curated YouTube playlist.
_Avoid_: Mirror deletions, prune (that is a Local feed's Retention), purge (that empties a bucket)

**Live stream**:
A Catalog item the Engine reports as a stream (`live_status`: upcoming, live, recording being processed, recorded). While upcoming or live it is listed in the Catalog with a badge but is not archivable: no policy or selection wants it and an Ephemeral feed does not list it; any Refresh, full or light, that sees it recorded makes it archivable. A Request is the exception: a Local feed never Refreshes, so a pushed stream is wanted at once and its archive job waits instead. An archive that meets a stream with no recording yet waits 30 minutes between tries, without counting attempts, for up to 48 h after the job was created, then fails; the archive job alone measures that ceiling (ADR 0014).
_Avoid_: Broadcast, premiere (a scheduled upload is an upcoming stream to Copycast), recording (that is what it becomes)

**Attachment**:
A file the show notes of an Episode embed or link to (an image, a PDF, an audio file), mirrored next to the Episode's media at archive time; the Mirror Feed's notes point at the local copy.
_Avoid_: Enclosure (that is the media), inline asset, media (that is the audio)

**Title override**:
A Mirror's own title, kept over the Source's across Refreshes and shown in the UI, the Mirror Feed and MCP; clearing it restores the Source's title.
_Avoid_: Rename (that is a Bucket), alias, custom name
_Avoid_: Blacklist, ignore list, ban

**Object store**:
The S3 bucket (`[storage] url`, SeaweedFS in compose) that holds every byte Copycast keeps under the old data directory's names: `LAYOUT_VERSION`, `engine/`, `feeds/{id}/feed.json`, `source/`, `media/`, `assets/`, plus staged Uploads under `uploads/`. Postgres can be rebuilt from it alone (ADR 0001, ADR 0019). Copycast proxies every object it serves; podcast apps never talk to it. `memory://` is the same code in-process, for tests only.
_Avoid_: Bucket (one Feed's share of the Object store), data directory (layout 1, before 2.0), disk, S3 (the protocol), MinIO

**Scratch**:
The worker's local directory (`[storage] scratch_dir`) where an archive job downloads, transcodes and tags in a directory of its own; only finished files leave it, uploaded to the Object store before the item is marked archived. Removed when the job ends, swept when a worker starts; never served, never backed up.
_Avoid_: tmp, temp dir, cache, staging

**Feed cache**:
Rendered Feed documents keyed by `(feed_id, revision)`: this api process's LRU in front of a Valkey every replica shares (`[cache] url`), with one render per revision across replicas. An accelerator only: losing it costs renders, never data.
_Avoid_: memcached (it is Valkey), CDN, feed store

**Engine**:
yt-dlp as used by Copycast: imported as a library, lockfile-pinned to one version (nightly channel by default), bumped by automation. Every download goes through it, RSS enclosures included; its version is recorded on every archived item and shown on the About page.
_Avoid_: Downloader, backend, scraper, yt-dlp binary

**User**:
A configured account: one `[[auth.users]]` table of `copycast.toml` (or `COPYCAST__AUTH__USERS`) with a username, an argon2id `password_hash`, an optional `totp_secret`, `admin` and `quota`. The username is the identity: renaming a user makes a new one. Never created through the API; a User removed from the file keeps their Feeds, shown to Admins as "owner removed" (ADR 0027).
_Avoid_: Account, login, profile, operator

**Admin**:
A User with `admin = true`: sees and acts on every User's Feeds (labelled with their Owner), and alone may `rebuild`, purge every Episode and set a Mirror's `engine_options`. Needs a TOTP secret to sign in on an https site. The open-mode and legacy single Admin are Admins too.
_Avoid_: Superuser, root, operator, owner (that is a Feed's User)

**Member**:
A User without `admin`: sees only their own Feeds; another User's Feed answers 404, an Admin-only capability 403.
_Avoid_: Regular user, guest, viewer

**Owner**:
The User a Feed belongs to (`feeds.owner`), and with it its Catalog, jobs, Uploads and bucket; also the User an API key acts as. Every Engine call made for a Feed uses its Owner's Cookie files and Defaults. Changed only by `copycast users reassign` (ADR 0028).
_Avoid_: Creator, author, user (say Owner when it is about a Feed)

**Session**:
A browser's sign-in: 32 random bytes in the `__Host-copycast_session` cookie (`copycast_session` on plain http), stored as a digest, sliding for `auth.session_days`, ended by *Sign out* or by a change of the User's password or TOTP secret at the next restart (ADR 0027).
_Avoid_: Login (the act), token, cookie (the carrier), Basic auth

**Principal**:
Whoever a request or a job acts as: a User through a Session or an API key, the open-mode Admin, or SYSTEM (the worker, the scheduler, startup and the public `/feeds` routes, which see every Owner). The services a request gets are scoped to it (`services_for`).
_Avoid_: Actor, caller, identity, account

**Auth mode**:
How Copycast authenticates, read from the configuration: `open` (neither users nor a password: no authentication, one implicit Admin, ADR 0004), `legacy` (only `COPYCAST__AUTH__PASSWORD`: one Admin signing in with the Operator password) or `users` (`[[auth.users]]` set).
_Avoid_: Auth on/off, security mode

**Quota**:
How much a User's Feeds may hold in the Object store (`quota`, else `auth.default_quota`, decimal units, `unlimited` by default), against their usage, the sum of their Feeds' `storage_bytes`. Shown as the Storage row of the status popover: ok, near from 90 %, Over quota from 100 % (ADR 0029).
_Avoid_: Limit, allowance, disk space

**Over quota**:
The state of a User whose usage has reached their Quota, and the badge of a Catalog item blocked by it (`blocked_reason: quota`, still wanted): no new download starts for them, manual archives and first plays answer `507 over-quota`, running jobs finish, nothing is deleted, and blocked items queue again by themselves when room frees up.
_Avoid_: Full (that is the Object store or Scratch), quota exceeded, blocked (in prose)

**Operator password**:
The legacy single Admin's password (`COPYCAST__AUTH__PASSWORD`, username `copycast` unless `COPYCAST__AUTH__USERNAME` says otherwise): setting it alone is legacy mode, where it signs in on the login page and still opens every feed over HTTP Basic. Exclusive with `[[auth.users]]`, and in users mode it opens nothing (ADR 0027).
_Avoid_: Admin password, account, session

**Feed credentials**:
A Feed's own random username and password, minted at creation, kept in Postgres and in `feed.json`, and rotated on demand. While authentication is on they open that Feed's RSS, media and assets and nothing else; the feed URL carries them as `user:pass@` and the UI shows them separately for apps such as Overcast.
_Avoid_: Feed token, feed secret, private URL, feed password (say the pair)

**Media key**:
A Feed's secret path segment, derived from its password, under which the Feed publishes its media and non-artwork assets while authentication is on (`/feeds/{id}/k/{key}/…`), so players that never answer a Basic challenge can stream (ADR 0020). Rotates with the Feed credentials; logged as `***`.
_Avoid_: Feed key (that is the multi-user plan's per-listener credential), token, signed URL

**API key**:
A bearer secret minted on the MCP page for one MCP client, shown once as `.env` lines (`COPYCAST_MCP_URL`, `COPYCAST_MCP_KEY`), stored as a digest, revocable on its own. The only credential the MCP mount accepts while authentication is on; on `/api` it reaches tool capabilities only. It acts as its Owner and never sees more than its Owner's own Feeds, an Admin's key included; its Owner alone lists and revokes it.
_Avoid_: Token, PAT, MCP password, service account

**Default**:
A User's setting in the Mirrors half of the Settings page (Refresh interval, metadata language, minimum length, the policy new Mirrors start from) that every Mirror they own inherits unless its own value is set; saved as it changes, stored in the Object store at `users/{username}/defaults.json` (`engine/defaults.json` before 3.0). A new User starts from the built-in values. A Mirror overrides it by setting a value and resets by clearing it.
_Avoid_: Global setting, preference, config (that is the environment)

**Minimum length**:
A Mirror's rule, or the Default, that keeps items shorter than N seconds Available: never archived by Backfill or Follow, still archivable by an explicit selection. Unknown lengths pass. Keeps YouTube Shorts out.
_Avoid_: Filter, Shorts rule, duration threshold

**Cookie file**:
A Netscape `cookies.txt` a User adds under *Authentications* on the Settings page so sites that demand a login (YouTube from a server's IP, Twitch, Vimeo, Mixcloud) accept downloads; stored in the Object store as `users/{username}/cookie-files/{name}.txt`, one row each. The Engine carries that User's jar, merged from their files at `users/{username}/cookies.txt` (yt-dlp gets a private Scratch copy), on every listing and fetch made for their Feeds or by them, and cookies the sites rotate there go back to their file. A service shows unlocked while a file holds its cookies. Never shown back, not even to Admins, never exposed to agents.
_Avoid_: Cookies setting, session file, YouTube login, credentials

**Bucket**:
A Feed's files: its prefix of the Object store (`feeds/{id}/`: media, Artwork, assets, `feed.json`, the Source copy and the worker's sidecars). Every Feed has exactly one. The Feeds page shows one card per bucket (files and size from `storage_summary`) in a collapsed *Buckets* box; a bucket's page lists every file with its kind, size, a padlock for the Episode it belongs to and a download link (`list_feed_objects`). *Purge* empties a bucket of every Episode that is not Locked and keeps the Feed (`clear_feed`); the Feed's own files are never purged. Deleting the Feed deletes its bucket.
_Avoid_: Storage, folder, volume, data directory; Bucket for a Feed with no Source (that is a Local feed); S3 bucket (say Object store)

**Scope**:
What an API key may do: `read` (read-only tools), `write` (everything but deletions and a Mirror's `engine_options`) or `full` (everything). Enforced over MCP and, since 3.0, on `/api` (by method: GET and HEAD need `read`, other methods `write`, destructive capabilities `full`); the argument checks (a Rolling or expiring backfill, `engine_options`, `autoprune_days`) run in the services, so both enforce them alike.
_Avoid_: Role, permission, level, grant

**Fetch guard**:
The check every Source-driven server-side fetch passes, Copycast's httpx client and the Engine's own connections alike: the host is resolved once, an address that is loopback, private, link-local or otherwise not public is refused (`422 destination-not-allowed`, its class only in the log), and the connection goes to the address that was checked. `[security] allow_private_hosts` lists the operator's exceptions; the Object store and the Feed cache never pass through it (ADR 0023).
_Avoid_: SSRF filter, firewall, allowlist (for the guard itself; its exceptions are `allow_private_hosts`)

**Cross-site request**:
A request with an unsafe method that a browser sends on behalf of another site (`Sec-Fetch-Site` other than `same-origin`/`none`, or a foreign `Origin`), refused with `403 cross-site-request` whether authentication is on or off. Any request but `/healthz` addressed to a host name that is not Copycast's is refused alike, as `403 unknown-host` (DNS rebinding, which over plain http sends neither header; `COPYCAST_ALLOWED_HOSTS`). Clients that send neither header are never refused as cross-site (ADR 0025).
_Avoid_: CSRF token (there is none), CORS (Copycast sends no CORS headers), origin check

## Conventions for agents

### Stack

Python 3.13, uv lockfile, FastAPI, Pydantic v2, pydantic-settings (`config/copycast.toml` plus `COPYCAST__SECTION__KEY` environment variables; `[[auth.users]]` is users mode, `COPYCAST__AUTH__PASSWORD` alone legacy mode, neither open mode), SQLAlchemy 2 async with psycopg 3, Alembic, structlog, httpx, lxml, sse-starlette, fastmcp, click, obstore (the Object store client), redis (the Feed cache client, against Valkey), argon2-cffi (password hashes), segno (the TOTP QR code); `yt-dlp[default]` from the lockfile; ffmpeg from the OS image; Postgres 17, SeaweedFS (S3), Valkey 9. Frontend: React 19, TypeScript, Vite 8, Tailwind v4, shadcn/ui, TanStack Router/Query/Table, openapi-typescript + openapi-fetch, vitest + MSW, Playwright; package manager pnpm 11 via corepack (never npm or yarn). Delivery: one image, Docker Compose (Tailscale sidecar or direct port), Kubernetes kustomize base, GitHub Actions.

### Repository map

```
src/copycast/domain/         pure rules: enums, ids, urls, listing, ordinals, selection, engine_options (the
                             per-Mirror allowlist), credentials (feed pairs, media keys, artwork MACs, session
                             tokens), destinations (which addresses the fetch guard refuses), users (usernames,
                             Principal), quota (sizes, states), totp (RFC 6238)
src/copycast/application/    models (the API/MCP contract), capabilities registry (with each one's Access), ports
                             (Engine), events, passwords (argon2), users (the UserDirectory), services/ (auth,
                             scoped and context: the Principal's Scope; quota, keyscope)
src/copycast/adapters/       api (FastAPI; objects.py proxies Object store objects, cache.py the Feed cache,
                             security.py the cross-site guard and security headers, principal.py the Principal
                             middleware, session_cookie.py, media_response.py, routes/auth.py and routes/play.py
                             the web player's media), mcp (fastmcp), db (SQLAlchemy, migrations;
                             repositories/secrets.py the instance secrets, reassign.py), storage (objects: the
                             Object store over obstore; layout: its keys; user_settings: the 3.0 copy of engine/;
                             scratch; descriptor; rebuild; push), engine (the
                             only importer of yt_dlp; netguard.py guards its connections), sources (RSS, discovery,
                             probe, iTunes; guard.py the fetch guard), feeds (RSS rendering), assets
src/copycast/worker/         runner, scheduler, progress, joblog, jobs/{refresh,archive_item,expand_request,prune,rebuild}
src/copycast/{app,cli,settings,logging,version}.py   composition root, click CLI, configuration
web/                         Vite app; web/openapi.json is committed and generated into src/api/schema.d.ts
deploy/                      compose/docker-compose.dev.yml (Postgres, SeaweedFS, Valkey for development),
                             compose/seaweedfs/entrypoint.sh, kubernetes/{base,overlays/example}
tests/                       unit, integration/{storage,worker,api,mcp,engine}, e2e, support (FakeEngine, Origin,
                             users, world), fixtures
scripts/                     smoke.sh, engine_version.py, engine_newest.py
docs/                        adr/ (decisions), operations.md (running it)
```

Import contracts (enforced by import-linter in `make lint`): `copycast.domain` imports no other copycast package; `copycast.application` never imports `copycast.adapters` or `copycast.worker`; `adapters.api` and `adapters.mcp` import `application` only; **only `copycast.adapters.engine` imports `yt_dlp`**. Tests and the worker talk to the Engine through the `Engine` Protocol in `application/ports.py`.

### One contract: capability → route → tool

Every user-facing operation is a service function registered with `@capability(name, request=..., response=...)` in `application/capabilities.py`, using Pydantic models from `application/models.py`. The HTTP route is a thin wrapper that sets `operation_id = name` and `openapi_extra={"x-capability": name}`; the MCP tool is a thin wrapper that sets `meta={"capability": name}` and takes the same request model. The convergence test (`tests/integration/mcp/test_convergence.py`) fails when a capability lacks its route, when a non-exempt capability lacks its tool, when a route or tool names an unregistered capability, or when a tool's input model is not the identical class. The UI consumes routes by capability name through `web/src/api/ops.ts`, checked against `web/openapi.json`. Never compose URLs in the UI: read `feed_url`, `media.url`, `item_url` from the models.

### Commands

```
make setup        uv sync + pnpm install from the lockfiles
make db-up        development Postgres (docker compose)
make dev          api (reload) + worker + Vite dev server
make test         unit + integration tests (Postgres via COPYCAST_TEST_DATABASE_URL or testcontainers)
make lint         ruff check, ruff format --check, import-linter
make typecheck    pyright strict over src
make openapi      regenerate web/openapi.json and the TypeScript schema (commit the result)
```

Use `uv run` for everything Python and `pnpm --dir web` for everything web. Run `make lint typecheck test` before finishing a change; `make openapi-check` fails when the committed OpenAPI document is stale.

### Adding a capability end to end

1. Add the name to `CAPABILITY_NAMES`, its `Access` to `ACCESS` (`public`, `member`, `owner_of_feed`, `admin` or `system`) and, if destructive, to `DESTRUCTIVE` (`application/capabilities.py`); add request/response models to `application/models.py`.
2. Implement the service function under `application/services/` decorated with `@capability(...)`; it receives a ServiceContext scoped to the caller's Principal (its units of work see only the Feeds the caller may see) and raises domain exceptions (`NotFound`, `Conflict`, `Unsupported`, ...) which the API maps to problem+json and MCP to `ToolError`.
3. Add the route in `adapters/api/` with `operation_id` and `x-capability`; add the tool in `adapters/mcp/` with `meta={"capability": name}` (or add the name to `TOOL_EXEMPT` / `INTERNAL` with a reason).
4. `make openapi`, then register the operation in `web/src/api/ops.ts` and build the screen.
5. Tests: a unit test for the service with `FakeEngine`, an API test with the `client` fixture, an MCP test with `mcp_client`; the convergence test picks the rest up.
6. Update the Language section here when the capability introduces a term, and write an ADR when it changes a decision.

### Dependency pinning

Everything is pinned exactly (Python `==` in pyproject.toml, exact versions in web/package.json, Docker images by digest, Actions by commit SHA, nixpkgs by flake.lock). Bump with `uv add "name==x.y.z"` or `pnpm --dir web add name@x.y.z`, never by loosening a pin; Dependabot opens the upgrade PRs. yt-dlp is moved only by `engine-bump.yml`. See docs/operations.md "Dependency pinning".

### Testing rules

- The Engine is never real in unit or integration tests: use `tests/support/fake_engine.py` (`script_listing`, `fail_next`, `block_fetches`). Real yt-dlp runs only in `tests/integration/engine` (`ffmpeg` marker, `network` marker gated by `COPYCAST_TEST_NETWORK=1`).
- Postgres tests are marked `integration` and get a per-test database cloned from a migrated template (`db` fixture); never share state between tests. Set `COPYCAST_TEST_DATABASE_URL` to skip testcontainers.
- Every test gets an Object store of its own (`store`, `layout` fixtures): a `memory://` store by default. `COPYCAST_TEST_S3_URL` (with `_ENDPOINT`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`) runs the whole suite against a real S3 bucket, a prefix per test; the `s3` marker runs SeaweedFS in a testcontainer. Assert against the Object store, never a local path; only Scratch is a directory.
- HTTP Sources come from `tests/support/origin.py` serving `tests/fixtures` (Range, ETag, scripted 304/5xx); never reach the internet in tests.
- The fetch guard is on in every test. An autouse fixture resolves names through `tests/support/dns.py` (address literals and `localhost` resolve, every other name does not; tests marked `network` keep the real resolver), and the suite's policy exempts only loopback (`TEST_ALLOW_PRIVATE_HOSTS` in `tests/conftest.py`), where `Origin` runs. A test that builds its own `Settings` passes `security={"allow_private_hosts": TEST_ALLOW_PRIVATE_HOSTS}`; a test that needs a name to resolve passes its own resolver.
- The `s3` testcontainer runs the real `deploy/compose/seaweedfs/entrypoint.sh` (keys enforced, the bucket made by its bootstrap) and checks which sockets it leaves beyond loopback; `tests/unit/test_compose.py` pins its bind flags and the `objects` network layout.
- Playwright specs import `test` and `expect` from `web/e2e/fixtures.ts`, never from `@playwright/test`: it fails any test whose pages met a Content-Security-Policy violation. Editing the inline theme script of `web/index.html` means updating `SPA_SCRIPT_HASHES` in `adapters/api/security.py` (`tests/unit/api/test_security.py` fails until it matches), and the web app never uses eval (zod runs jitless, `web/src/lib/zod.ts`).
- Tests run in open mode by default, as the open-mode Admin `copycast`, who owns everything they create. Users-mode tests use `tests/support/users.py` (`TEST_HASHER`, cheap argon2 parameters; `USERS` root, alice and bob; `login`, `mint_key`, `as_principal`) and the `world` fixtures of `tests/integration/conftest.py` (`world`, `alice_client`, `bob_client`, `root_client`: Bob's Mirror, Local feed, job, Upload, key and failed Refresh). Groups of tests never edit `tests/support/**` or a `conftest.py` to add a private fixture; define it in the test module. A new `/api` route lands in the isolation matrix (`tests/integration/api/test_isolation_matrix.py`) or its capability-less list, and `tests/unit/test_raw_uow_ban.py` forbids the raw unit-of-work factory and SYSTEM services in `adapters/api` and `adapters/mcp` outside its allowlist.
- The e2e stack runs in users mode (`tests/e2e/config/copycast.toml`: admin, alice, bob, with cheap hashes): the httpx smoke signs in, and the Playwright `setup` project signs each User in to a `storageState` file under `web/e2e/.auth/`.
- Rendered feeds are compared canonically (`tests/support/xml.py`) against golden files; regenerate with `make golden-regen` and review the diff.
- Keep coverage above the gates (85 % overall, 95 % for `copycast.domain`).

### ADR index

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-postgres-is-truth-data-dir-rebuildable.md) | Postgres is the runtime truth; the data directory is complete enough to rebuild it |
| [0002](docs/adr/0002-engine-in-process-lockfile-pinned-nightly.md) | yt-dlp runs in process, pinned by the lockfile, nightly channel by default |
| [0003](docs/adr/0003-one-contract-ui-api-mcp.md) | UI, API and MCP share one capability contract |
| [0004](docs/adr/0004-network-is-the-security-boundary.md) | No authentication: the network (Tailscale) is the boundary (open mode since ADR 0027) |
| [0005](docs/adr/0005-modular-monolith-two-processes-one-image.md) | Modular monolith, two processes, one image |
| [0006](docs/adr/0006-image-tags-and-engine-bump-automation.md) | Image tags and automated engine bumps |
| [0007](docs/adr/0007-inbox-and-requests.md) | Bucket feeds fed by Requests |
| [0008](docs/adr/0008-ordinals-source-numbering-selections.md) | Ordinals, Source numbering and selections |
| [0009](docs/adr/0009-never-delete-tombstones.md) | Copycast never deletes from a Mirror; user deletions leave Tombstones |
| [0010](docs/adr/0010-download-counting-rule.md) | What counts as a download |
| [0011](docs/adr/0011-optional-authentication-for-direct-deployments.md) | Optional authentication: operator password (superseded on the UI and API by ADR 0027), feed credentials, MCP API keys |
| [0012](docs/adr/0012-mirror-modes-rolling-and-automatic.md) | Mirror modes: Rolling windows and Automatic archives narrow ADR 0009 |
| [0013](docs/adr/0013-light-refresh-on-fetch-and-stable-media-urls.md) | A feed fetch runs a Light Refresh inline; media URLs are fixed at listing time; the api logs its own requests |
| [0014](docs/adr/0014-live-streams-archived-once-recorded.md) | Live streams are archived once their recording is published; an archive that meets one waits, then fails |
| [0015](docs/adr/0015-serial-inboxes-from-uploads.md) | Serial Buckets: an agent uploads files over HTTP and gives their listening order |
| [0016](docs/adr/0016-ephemeral-or-archive-and-the-archive-flag.md) | Two policies, Ephemeral and Archive (with or without Backfill); the Archive flag keeps an Episode for good |
| [0018](docs/adr/0018-inboxes-become-buckets-with-uploads.md) | Inboxes are renamed Buckets; uploads join any Bucket in natural file-name order, dated by arrival |
| [0019](docs/adr/0019-object-store-scratch-and-a-shared-feed-cache.md) | Every byte lives in an S3 Object store; the worker transcodes in Scratch; first plays hold the line on a reserved slot; feeds are cached in a shared Valkey |
| [0020](docs/adr/0020-media-keys-in-enclosure-urls.md) | While auth is on, enclosures and non-artwork assets are published under a media key derived from the feed's password |
| [0021](docs/adr/0021-every-feed-has-a-bucket-and-locks.md) | Every Feed has a bucket; the Feed with no Source is a Local feed; the Lock (ex Archive flag) alone protects an Episode, and the Archive policy Locks what it archives |
| [0022](docs/adr/0022-public-artwork-under-opaque-tokens.md) | Public Artwork under opaque `/art/` tokens keyed with an instance secret; one 401 for every refusal under `/feeds`; every feed carries directory block tags |
| [0023](docs/adr/0023-fetch-guard-on-every-source-driven-fetch.md) | The fetch guard: no Source-driven fetch, the Engine's connections included, reaches an internal address unless `allow_private_hosts` exempts it |
| [0024](docs/adr/0024-per-mirror-engine-options-are-an-allowlist.md) | A Mirror's `engine_options` are an allowlist; setting them over MCP needs a `full` key |
| [0025](docs/adr/0025-the-browser-boundary.md) | Cross-site writes and foreign Host names are refused; security headers (CSP sandbox on `/feeds` and `/art`); trusted forwarded headers; no secret in a log line |
| [0026](docs/adr/0026-seaweedfs-internal-apis-on-loopback-and-the-objects-network.md) | SeaweedFS keeps master, volume and filer on loopback; its gateway is reachable only on the internal `objects` network |
| [0027](docs/adr/0027-users-from-the-config-file-sessions-and-totp.md) | Users come from the config file; the browser signs in to a session (TOTP for Admins on https); `/api` takes sessions and keys, never Basic; open, legacy and users modes |
| [0028](docs/adr/0028-feeds-are-private-to-their-owner.md) | Feeds are private to their Owner (404, never 403); Admins see all, keys never more than their owner; owner-salted ids in users mode; per-user Defaults and Cookie files |
| [0029](docs/adr/0029-storage-quotas.md) | Storage quotas: at the limit new downloads wait, blocked and still wanted; 507 from manual archives, first plays and Uploads; nothing is deleted |
