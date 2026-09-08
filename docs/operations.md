# Operating Copycast

This is the runbook: first run, configuration, Kubernetes, backup and restore, rebuilding the
database from disk, and engine bumps and rollbacks. The product language (Mirror, Inbox,
Refresh, ...) is defined in [AGENTS.md](../AGENTS.md).

## Shape of a deployment

One image, `ghcr.io/flrntdfr/copycast`, run as two processes plus Postgres 17:

| Process | Command | Port | Role |
|---|---|---|---|
| api | `copycast api` | 8080 | web UI, HTTP API, MCP at `/mcp`, published feeds and media, SSE events |
| worker | `copycast worker` | 8081 (health only) | Refreshes, downloads through the engine, Request expansion, pruning, rebuilds |

Both share the same Postgres database and the same data directory (`/data` in the image).
There is exactly one worker: it holds a session-level advisory lock and a second worker exits
with a message. By default Copycast has no authentication: put it behind Tailscale (the
compose `tailscale` profile, the Kubernetes Tailscale Ingress) or on a trusted network, or
switch authentication on (below) behind TLS.

## First run with Docker Compose

```bash
cp .env.example .env
mkdir -p data config && chown -R 1000:1000 data
docker compose up -d
docker compose logs -f api worker
```

`.env` chooses the profile (`tailscale` or `direct`), the Postgres password and the public
base URL. The base URL is embedded in every feed and media link, so it must be the address
podcast clients will use: `https://copycast.<tailnet>.ts.net` for the Tailscale profile,
`http://<host>:8080` for the direct profile.

Both processes exit with code 2 and a single actionable line when the configuration is
unusable: an unwritable data directory (the line contains the `chown` command to run), a
`LAYOUT_VERSION` mismatch, a rejected engine option, or a malformed base URL.

Readiness: `GET /healthz/ready` on the api (8080) and on the worker (8081) answers 200 with
`{status, checks{db, schema, data_dir, layout, worker_seen_at}}` (the worker adds
`scheduler` and `ffmpeg`), 503 when anything is degraded. `GET /healthz/live` is a plain
liveness check. `/api/about` shows the app version, the engine version and channel, ffmpeg,
the layout version and storage totals.

## Configuration

Configuration is read, in increasing precedence, from built-in defaults, the TOML file named
by `COPYCAST_CONFIG`, and environment variables. A missing TOML file is tolerated.

### `config/copycast.toml`

```toml
base_url     = "http://localhost:8080"
data_dir     = "./data"
database_url = "postgresql+psycopg://copycast:copycast@localhost:5432/copycast"   # postgresql:// is rewritten

[refresh]
# interval_hours: ignored since 1.2; the Refresh interval is a Settings-page default
fetch_cooldown_minutes = 5      # minimum gap between feed-fetch-triggered Refreshes
concurrency            = 2      # parallel engine jobs in the worker

[engine]
channel = "nightly"             # informational: nightly | stable

[engine.options]                # raw yt-dlp defaults applied to every job

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
| `COPYCAST__DATA_DIR` | `./data` (`/data` in the image) | data directory (also settable in the TOML) |
| `COPYCAST_TEST_DATABASE_URL` | unset | tests: use this Postgres instead of testcontainers |
| `COPYCAST_TEST_NETWORK` | unset | tests: `1` enables the network-marked tests |

Compose-only (interpolated by `docker compose`, see `.env.example`): `COMPOSE_PROFILES`,
`TS_AUTHKEY`, `POSTGRES_PASSWORD`, `COPYCAST_BASE_URL`, `COPYCAST_AUTH_PASSWORD`,
`COPYCAST_AUTH_USERNAME`, `COPYCAST_IMAGE`, `COPYCAST_PORT`, `COPYCAST_UID`, `COPYCAST_GID`.

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
the old one. Overcast, Pocket Casts and AntennaPod send the pair for episode downloads too;
Apple Podcasts fetches the feed but not the audio.

**API keys.** Minted and revoked on the *API keys* page (`/api/keys`), shown once, stored as a
SHA-256 digest, with a scope: `read` (read-only tools), `write` (everything but deletions) or
`full` (everything, including `delete_feed`, `delete_item`, `prune_inbox`, and setting an
Inbox's Retention through `create_inbox` or `update_inbox`, since autoprune deletes). Every tool
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

### The data directory

```
data/LAYOUT_VERSION                     "1"; written on first start, a mismatch refuses to start
data/engine/cookies.txt                 the engine's cookie file (optional, mode 0600), see below
data/feeds/<feed_id>/feed.json          descriptor of the feed, its policy, items, assets and requests
data/feeds/<feed_id>/source/            verbatim Source XML or the last engine listing
data/feeds/<feed_id>/media/             audio, <item_id>.info.json, <item_id>.item.xml (original RSS item)
data/feeds/<feed_id>/tmp/               partial downloads; safe to delete while the worker is stopped
data/feeds/<feed_id>/assets/            Artwork, chapters, transcripts
```

The descriptor is rewritten after every change of intent and is what `copycast rebuild`
reads. Nothing outside the worker writes into `media/` or `tmp/`.

## Mirror modes

A Mirror's Settings tab (and the wizard) offers the Backfill as tabs:

| Mode | Archives | Deletes |
|---|---|---|
| Everything | every listed item, then whatever Follow brings | never |
| Rolling N | the newest N by publication, at every Refresh | archived items outside the window, at the next Refresh and at once when you switch to it |
| Automatic | nothing until a podcast app asks for an Episode's media; the request waits up to two minutes, then answers `503 Retry-After: 30` while the download continues | Episodes idle for *Retention* days after their last download (7 by default; empty keeps forever), checked by the worker's prune pass |
| Selection | exactly the numbers given | never |

New Mirrors start from the operator's default policy (*Policy for new Mirrors* on the Settings
page, stored with the other defaults; Automatic with a 7-day retention out of the box), both in
the wizard and for an MCP `create_mirror` without `backfill`.

Switching modes previews the change (`POST /api/mirrors/{id}/preview`) and asks for
confirmation when archived Episodes would be deleted; rolled-out and expired Episodes leave
Tombstones and can be archived again on purpose. An Automatic feed keeps listing expired and
deleted Episodes and downloads them again when a podcast app asks. The Mirror header menu
offers *Archive all Available…* (queues everything not archived yet, Shorts and Tombstones
included, with the option to switch the Mirror to Everything) and *Retry N failed*; the same
actions are the `archive_available` and `retry_failed` tools and routes. Over MCP, `create_mirror` and
`update_mirror` refuse Rolling and expiring Automatic modes without a `full` key. Latest N
stays on Mirrors that already use it but is no longer offered ([ADR 0012](adr/0012-mirror-modes-rolling-and-automatic.md)).

An Automatic Mirror's feed lists every item the Source lists; the ones not archived yet carry
a placeholder `.mp3` enclosure whose length is estimated from the duration at 128 kbit/s
(apps refuse 0-byte enclosures), and the media route serves the real file (any container)
under that URL once archived.

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

## Pull to refresh, directory links and playlists

- **Pull to refresh.** A podcast app fetching a Mirror Feed queues a Refresh and waits for it
  (up to 20 s) before answering, so the new Episodes are in that very answer; within the
  cooldown (`refresh.fetch_cooldown_minutes`, 5 by default) the feed answers at once. Paused
  Mirrors never refresh on a fetch.
- **Apple Podcasts and Spotify links.** Pasting a `podcasts.apple.com` link resolves the feed
  through Apple's lookup API; an `open.spotify.com/show/…` link is resolved by the show's name
  through Apple's directory (an exact match is used, otherwise every hit is a candidate to pick
  from). `probe_source` and `create_mirror` accept both, so agents can too.
- **Playlists kept in sync.** A Mirror with *Stay in sync with the Source* deletes an Episode
  when its item leaves the Source instead of Delisting it (a Tombstone, so the video comes back
  as Available if re-added). The Inboxes page's *Capture from the YouTube app* card creates such
  a Mirror from a private playlist in Automatic mode; with your cookies stored, sharing a video
  to that playlist from the YouTube app puts it in the feed.
- **Adding a Source** happens in a dialog from the Mirrors page bar (a URL, an Apple Podcasts or
  Spotify link, or a name to search) or the command palette; the Mirror takes the operator's
  default policy, changed afterwards in its Settings tab.

## Show notes, OPML and the player

- **Attachments.** When an Episode is archived, the images its show notes embed and the
  files they link to (images, PDFs, audio; up to 20 per Episode, 5 MiB per image, 20 MiB per
  file) are mirrored as `attachment` assets under `assets/{item}.attachment.{slot}.{ext}`,
  and the Mirror Feed's description points at the local copies. A file that cannot be fetched
  is recorded as a failed asset and its original URL stays in the notes.
- **OPML.** *Export OPML* on the Settings page (`GET /api/feeds.opml`) lists every feed with
  its credentialed URL, to subscribe to all of them in one go.
- **Player.** The web player has a seek bar with chapter marks (from the Episode's chapters
  asset), skip back 15 s / forward 30 s, speed from 0.75× to 2×, volume and mute, and keyboard
  shortcuts (space, arrows, `m`, `[` and `]`); speed and volume are remembered per browser.
- **Title.** The pen next to a Mirror's title edits it in place; Enter saves, Escape cancels.
- **Descriptions on demand.** Expanding a Catalog row without a description asks the Source for
  it (and the exact date and artwork) right there; `fetch_item_metadata` does the same for
  agents.

## Titles, artwork and crawlers

- A Mirror's Settings tab has a *Title* field: a title of your own is shown in the UI, the
  Mirror Feed and MCP and survives Refreshes (`title_override`; the Source's title stays in
  `source_title`); *Use Source title* clears it.
- Artwork assets (`/feeds/{id}/assets/*.artwork.*`) and the Copycast logo
  (`/feeds/copycast-artwork.png`, the artwork of an Inbox feed without its own) answer without
  credentials even while authentication is on: podcast apps fetch images bare, and the URLs
  stay unguessable. Media, feeds, chapters and transcripts keep the feed's pair.
- `/robots.txt` disallows everything and every response carries
  `X-Robots-Tag: noindex, nofollow, noarchive`; the UI shell adds the equivalent meta tag.
- The About page's trash button next to the totals deletes every archived Episode of every
  feed after a dry run (`POST /api/admin/purge`, `dry_run` true by default); feeds, Catalogs,
  artwork and credentials stay, and the Episodes leave Tombstones.

## Settings page: defaults every Mirror may override

Two settings live in the data directory (`data/engine/defaults.json`, `PUT
/api/settings/defaults`) rather than in the environment, because they are policy the operator
changes from the UI:

| Default | Effect | Per-Mirror override |
|---|---|---|
| Metadata language (`fr`, `pt-BR`) | yt-dlp asks YouTube for that language instead of English, so a French channel's titles and descriptions stay French | the Mirror's *Metadata language* field (`preferred_language`; `language` stays what the Source reports) |
| Minimum length (minutes) | items shorter than that are listed but never archived by Backfill or Follow; unknown lengths pass; explicit selections still work (keeps Shorts out) | the Mirror's *Minimum length* field (`min_duration_seconds`) |
| Policy for new Mirrors | the Backfill a new Mirror starts from: Automatic (with its retention), Everything or Rolling N | the Mirror's Settings tab, or `backfill` in `create_mirror` |
| Refresh every (hours) | how often the scheduler Refreshes a Mirror (24 by default; replaces the `refresh.interval_hours` config key) | the Mirror's *Refresh every* field (`refresh_interval_hours`) |

A Mirror's Settings tab shows "Using the default" when a field is empty, marks a value that
diverges from the default, and offers *Use default*, which clears the override (sent as
`null`). Changing a default applies from each Mirror's next Refresh. Both capabilities have
routes but no MCP tools.

Engine-sourced Episodes (YouTube, SoundCloud, Inbox Requests) carry the Source's page URL at
the end of their description, since most podcast apps never show the item's `<link>`.

`search_videos` (`GET /api/search/videos`, an MCP tool, and the wizard's search step) finds
videos on YouTube through the engine; an agent asked for "the last WWDC live from Gruber"
searches, picks the newest hit and pushes its URL with `add_to_inbox`.

## YouTube and other sites that need a login

YouTube answers requests from a server's address (a Linode or Hetzner IP, say) with
"Sign in to confirm you're not a bot", and the item fails permanently. The fix yt-dlp
recommends is the cookies of a logged-in browser session:

1. In a browser, open a private window and sign in to youtube.com.
2. Export its cookies with a "Get cookies.txt LOCALLY" extension (Netscape format).
3. Close the private window without signing out, so YouTube does not rotate the session.
4. Paste or pick the file on the **Settings** page (`PUT /api/engine/cookies`).
5. **Retry** the failed Episodes from the Catalog.

The image also ships `deno`, the JavaScript runtime yt-dlp needs for YouTube's signature
and "n" challenges; without it YouTube extraction degrades to a warning-laden fallback that
is refused far more often. The file is stored at `data/engine/cookies.txt` with mode 0600, never shown again, and
handed to yt-dlp as `cookiefile` on every listing and fetch, in both processes. Each call
works on a private copy and the stored file is replaced atomically when the site rotated a
cookie, so two jobs never tear it. An explicit `cookiefile` under `[engine.options]` still
wins. Remove the file from the same page to go back to anonymous fetches. An agent cannot
read or replace it: the three capabilities have routes but no MCP tools.

What lands in `media/`: mp3 and m4a enclosures and streams are copied byte for byte; AAC in
a video container is remuxed to m4a; Opus, Vorbis, FLAC, WAV and the like are transcoded
once to mp3 at 192 kbit/s, so every Episode plays in every podcast app and carries its
chapters. Cover art already embedded in a file is kept; the feed's episode image is then
stored as the Episode's Artwork asset next to it.

Thumbnails never fail an archive: a CDN that serves a JPEG under a `.png` URL is detected
from the bytes and renamed before ffmpeg converts it, and an image ffmpeg still refuses only
costs the embedded artwork, with a warning in the job log.

## Kubernetes

`deploy/kubernetes/base` is a kustomize base; `deploy/kubernetes/overlays/example` shows what
to change. Prerequisites: the [CloudNativePG](https://cloudnative-pg.io) operator and the
[Tailscale Kubernetes operator](https://tailscale.com/kb/1236/kubernetes-operator).

```bash
cp -r deploy/kubernetes/overlays/example deploy/kubernetes/overlays/mine
$EDITOR deploy/kubernetes/overlays/mine/copycast.toml      # base_url = https://copycast.<tailnet>.ts.net
kubectl apply -k deploy/kubernetes/overlays/mine
kubectl -n copycast get pods,ingress
```

What the base creates in namespace `copycast`:

- `Cluster copycast-db` (CNPG, one instance, 10Gi). The operator writes the connection URI
  into secret `copycast-db-app` (key `uri`), which both Deployments read.
- `PersistentVolumeClaim copycast-data` (ReadWriteOnce, 200Gi). ReadWriteOnce is enough as
  long as `copycast-api` has one replica: the worker carries a required pod affinity to the
  api pod so both land on the node that has the volume. Raise `api.replicas` only with a
  ReadWriteMany storage class.
- `Deployment copycast-api` (1 replica, RollingUpdate, migrates at startup) and
  `Deployment copycast-worker` (1 replica, Recreate, `COPYCAST_AUTO_MIGRATE=false`: it waits up
  to 5 minutes for the api to migrate, then exits and is restarted). Probes on
  `/healthz/ready` and `/healthz/live`.
- `Service copycast` (ClusterIP 8080) and `Ingress copycast` with `ingressClassName: tailscale`
  and host `copycast`, so the app is `https://copycast.<tailnet>.ts.net` inside your tailnet.

High availability is active-passive by construction: one worker, one data volume. A node
failure is recovered by rescheduling both pods together.

## Backup and restore

Back up the data directory. It is sufficient: media, sidecars, original Source XML, assets
and the `feed.json` descriptors reconstruct the database. Optionally add a `pg_dump` to keep
telemetry (job history, refresh runs, download counters, engine versions):

```bash
docker compose exec postgres pg_dump -U copycast -Fc copycast > copycast-$(date +%F).dump
```

Restore: put the data directory back, start Postgres, then either restore the dump
(`pg_restore -U copycast -d copycast copycast.dump`) or run a rebuild (below). Stop the
worker before touching files under `data/feeds/*/media`.

## Rebuilding the database from disk

`copycast rebuild` reconstructs feeds, items, assets and requests from `feed.json` and the
sidecars, reconciles `media/` with the item rows (audio present ⇒ archived; file without a
row ⇒ a row from its `.info.json` / `.item.xml`), stats the assets, recounts storage and
recreates the default Inbox. It is idempotent and refuses to run while the worker holds its
lock, so stop the worker first.

```bash
docker compose stop worker
docker compose run --rm worker rebuild          # add --yes to also delete rows absent from disk
docker compose start worker
```

Lost by a rebuild: jobs and their logs, refresh runs, heartbeats, engine version history and
download counters. Everything else, including item ids and ordinals, comes back identical.

## Engine bumps and rollback

The engine (yt-dlp) is pinned in `uv.lock`. `engine-bump.yml` runs every six hours, pins the
newest release of the configured channel (`vars.ENGINE_CHANNEL`, nightly by default), runs
the engine tests, and pushes the lock change to `main`; `image.yml` then builds and publishes:

| Tag | Moves? | Use |
|---|---|---|
| `latest` | yes | the newest successful build |
| `1.2.0` | yes, on every engine bump | "the current 1.2.0" |
| `1.2.0-yt2026.8.19` | never | exactly this app + engine combination |
| `sha-<short>` | never | one commit |

Every image is smoke-tested (`scripts/smoke.sh`) before its tags move. To roll back an engine
that broke a site, pin the previous immutable tag in `.env`
(`COPYCAST_IMAGE=ghcr.io/flrntdfr/copycast:1.2.0-yt<previous>`) or in the kustomize overlay
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
(echoed as `X-Request-ID`), `job_id`, `mirror_id` and `inbox_id` bound where applicable;
health-check access lines are suppressed. Job progress and state changes are also streamed
to the UI over `GET /api/events` (SSE) and stored per job in `job_log_lines` for the Jobs
page.
