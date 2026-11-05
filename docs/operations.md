# Operating Copycast

This is the runbook: first run, configuration, the Object store, upgrading to 2.3.0,
Kubernetes, backup and restore, rebuilding the database from the Object store, and engine
bumps and rollbacks. The
product language (Mirror, Local feed, bucket,
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
contains the `chown` command to run), a rejected engine option, a malformed base URL, or a
bad entry in `[security] allow_private_hosts`, `COPYCAST_FORWARDED_ALLOW_IPS` or
`COPYCAST_ALLOWED_HOSTS`.

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

[security]                      # the fetch guard ("Fetch guard and browser defences")
allow_private_hosts = []        # hostnames or CIDRs a server-side fetch may reach although
                                # they are not public, e.g. ["nas.lan", "192.168.1.0/24"]
```

Every TOML key can be overridden with `COPYCAST__<SECTION>__<KEY>` (double underscores):
`COPYCAST__BASE_URL`, `COPYCAST__DATABASE_URL`, `COPYCAST__REFRESH__CONCURRENCY`,
`COPYCAST__AUTH__PASSWORD`, ...

`[engine.options]` takes raw yt-dlp options (for example `cookiefile`, `proxy`,
`sleep_interval`). Options Copycast owns (output paths, format selection, postprocessors,
hooks, ...) are rejected at startup with exit code 2. Everything else is accepted here, since
the operator owns this file: cookies, proxies, a YouTube PO token and any credential belong
here and never per Mirror.

A Mirror's own `engine_options` (the API and MCP) pass an allowlist instead
([ADR 0024](adr/0024-per-mirror-engine-options-are-an-allowlist.md)). They cover format
order (`format_sort`, `format_sort_force`, `prefer_free_formats`), bounded pacing and retries
(`ratelimit`, `throttledratelimit`, the `sleep_interval*` family, `retries`,
`fragment_retries`, `extractor_retries`, `file_access_retries`, `socket_timeout`,
`concurrent_fragment_downloads` 1 to 16, `skip_unavailable_fragments`), subtitles
(`writeautomaticsub`, `subtitlesformat`, and `subtitleslangs` as language codes, never
patterns), `geo_bypass`, `geo_bypass_country`, `geo_bypass_ip_block`, `age_limit`, and
`extractor_args` for `youtube` (`lang`, `player_client`, `player_skip`, `skip`, `formats`),
`youtubetab` (`approximate_date`, `skip`) and `generic` (`impersonate`), each value a list of
strings. Any other key answers `422 engine-option-rejected` with `keys` naming every refused
path (`netrc_cmd`, `extractor_args.youtube.po_token`). `extractor_args` merge per argument,
so a Mirror's `youtube.player_client` keeps the operator's `youtube.po_token`. `retries`,
`fragment_retries`, `socket_timeout`, `writeautomaticsub` and `subtitlesformat` only affect
listings: Copycast sets its own on a fetch. Over MCP, setting a Mirror's `engine_options`
needs a `full` key.

### Environment-only variables

| Variable | Default | Meaning |
|---|---|---|
| `COPYCAST_CONFIG` | `./config/copycast.toml` (`/config/copycast.toml` in the image) | TOML file to read |
| `COPYCAST_BIND` / `COPYCAST_PORT` | `0.0.0.0` / `8080` | api listen address and port |
| `COPYCAST_FORWARDED_ALLOW_IPS` | unset (uvicorn's `FORWARDED_ALLOW_IPS`, else `127.0.0.1`) | the proxies whose `X-Forwarded-For` and `X-Forwarded-Proto` the api believes: comma-separated IPs or CIDR networks, or `*`. Set it to the proxy's network behind Traefik or any proxy that is not on loopback (the Tailscale sidecar is). Ignored, without a word, when it is written in the TOML file; a bad entry exits 2 |
| `COPYCAST_ALLOWED_HOSTS` | unset | host names besides `base_url`'s that a browser or a client may reach Copycast by (a MagicDNS short name, a LAN alias, a compose or Kubernetes service name), comma-separated, or `*` to turn the check off; IP addresses and `localhost` always pass ("Fetch guard and browser defences" below). Unlike the variable above, the TOML file can set it too, like the other flat knobs |
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
The repository's `docker-compose.yml` passes `COPYCAST_FORWARDED_ALLOW_IPS` and
`COPYCAST_ALLOWED_HOSTS` from `.env` to both processes as they are. A compose file of your own
does so only if its `environment:` names them or an `env_file:` loads them ("Upgrading to
2.3.0").

## Authentication

Off unless `COPYCAST__AUTH__PASSWORD` is set (`COPYCAST_AUTH_PASSWORD` in the compose `.env`).
Basic auth is only a gate over TLS: with the `direct` profile put a TLS-terminating proxy in
front. The decision and its trade-offs are
[ADR 0011](adr/0011-optional-authentication-for-direct-deployments.md). Once on:

| Surface | Credential | Notes |
|---|---|---|
| Web UI, `/api/*`, `/api/events` | the operator pair (HTTP Basic) | `auth.username` defaults to `copycast`; the browser prompts once |
| `/feeds/<id>.xml`, media, assets | that feed's own pair, or the operator pair | one pair opens one feed; every refusal is the same 401 with a `Basic` challenge |
| `/feeds/<id>/k/<key>/media/…`, `…/assets/…` | the feed's media key in the path (or a pair) | what the feed publishes its enclosures, chapters, transcripts and attachments under |
| `/art/<asset_id>-<mac>.<ext>`, `/feeds/copycast-artwork.png` | none | Artwork; the MAC is the credential |
| `/uploads/<id>/<token>` | the one-time token | an Upload's `PUT` |
| `/mcp` | an API key as `Authorization: Bearer cck_…` | keys only; the operator pair is refused |
| `/healthz/*` | none | probes and healthchecks stay open |

Under `/feeds` every refusal gets the same 401, detail "this feed requires its username and
password": a missing or wrong pair, another feed's pair, a wrong media key, or a feed, item or
asset that does not exist. No answer tells a stranger what this server mirrors
([ADR 0022](adr/0022-public-artwork-under-opaque-tokens.md)). The operator's 401 on `/api`
still says "wrong username or password".

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
`***`. A reverse proxy's own access log records it with the path (see "Upgrading to 2.3.0"
for Traefik). Bare `/feeds/<id>/media/…` URLs still work with the pair. The upgrade to 2.0.2
moved every enclosure URL once, so apps may have downloaded Episodes they already had again
(ADR 0020).

**Artwork** is published at `/art/<asset_id>-<mac>.<ext>` in both auth modes, in every feed
and in the API's `AssetRead.url`. The `mac` is an HMAC of the asset id under the instance
secret, 32 random bytes the api mints on its first start in the Postgres table
`instance_secrets`. The URL needs no credential (podcast apps fetch images bare) and cannot be
computed from a Source URL. Every bad token is the same 404, and images are served with
`Cache-Control: public, max-age=86400`. The old path `/feeds/<id>/assets/*.artwork.*` takes
the pair or the media key like any asset while authentication is on.

**API keys.** Minted and revoked on the *MCP* page (`/keys` in the UI, `/api/keys` in the API),
shown once as two `.env` lines (`COPYCAST_MCP_URL=<base URL>/mcp` and `COPYCAST_MCP_KEY=cck_…`),
stored as a SHA-256 digest, with a scope: `read` (read-only tools), `write` (everything but deletions) or
`full` (everything, including `delete_feed`, `delete_item`, `prune_bucket`, and setting a
Local feed's Retention through `create_bucket` or `update_bucket`, since autoprune deletes). Every tool
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

## Fetch guard and browser defences

**Fetch guard.** Every fetch a Source drives refuses an address that is not public: the probe
and the feeds a page advertises, directory and iTunes lookups, RSS Refreshes and Light
Refreshes, the YouTube Atom feed, Request expansion, Artwork, chapters, transcripts,
Attachments, and every connection the Engine opens (redirects, manifests, fragments,
thumbnails). That covers loopback, private (RFC 1918), CGNAT `100.64/10`, link-local
(`169.254/16`, `fe80::/10`), `0/8` and `::`, multicast, reserved and broadcast, `fc00::/7`,
`fec0::/10`, 6to4, NAT64 local-use and the IPv4-mapped or NAT64 forms of these. Schemes other
than `http` and `https` are refused too. Every redirect hop is checked, and the address
connected to is the one that was checked, so a DNS answer that changes in between does not get
through. A name that resolves to any refused address is refused as a whole
([ADR 0023](adr/0023-fetch-guard-on-every-source-driven-fetch.md)). The Object store and the
Feed cache are not Sources and never pass through it.

- **Exceptions.** `[security] allow_private_hosts` lists hostnames, matched exactly
  (case-insensitive, trailing dot ignored), which may then resolve to any address, and IP
  networks in CIDR form (a bare address is one host), which are exempt whatever name led to
  them. In the environment it is a JSON list:
  `COPYCAST__SECURITY__ALLOW_PRIVATE_HOSTS='["nas.lan","192.168.1.0/24"]'`. A bad entry exits
  2 with one line. List a NAS, a self-hosted podcast server or a compose service you mirror
  from; for `make dev` against a local test server, `["localhost"]`. The Engine's proxy
  ("Proxies" below) is judged as the peer the Engine connects to, so a proxy on a private
  address must be listed too.
- **What a refusal looks like.** The probe, `create_mirror` and a retarget answer
  `422 destination-not-allowed` ("… is not an allowed destination: it resolves to a
  non-public address (an operator can exempt it with security.allow_private_hosts)"). Which
  kind of address it was, and the address, are only in the `fetch.destination_refused` log
  line. In the worker a refused Refresh, archive or Request expansion fails once, for good
  (no retry), and the Mirror keeps serving what it has. A refused Artwork or Attachment fails
  only that asset. A Request with an internal URL is accepted and fails at expansion.
- **Proxies.** Copycast's own fetches (RSS, the probe, Atom, Artwork, Attachments) ignore
  `HTTP_PROXY` / `HTTPS_PROXY` and have no proxy setting at all: they always connect
  directly. The Engine uses `proxy` from `[engine.options]` or, when that is unset,
  `HTTP_PROXY` / `HTTPS_PROXY` from the worker's environment (yt-dlp reads them itself). That
  proxy is judged as the peer, and every destination the Engine sends through it, each
  redirect hop included, is judged on its own before the proxy hears of it (HTTP and SOCKS
  proxies alike). The proxy resolves the name itself, so the address cannot be pinned: a name
  that resolves in the worker is judged by what it resolves to there, and one that does not
  resolve there passes, leaving it to the proxy's own network.
- **Not covered:** a process yt-dlp starts itself (ffmpeg as the downloader of a stream it
  cannot fetch natively) connects outside the guard.

**Cross-site requests and host names.** Whether authentication is on or off, the api refuses:

- any request but `/healthz` (any method, any client) whose `Host` is not `base_url`'s host, a
  name in `COPYCAST_ALLOWED_HOSTS`, an IP address or `localhost`: `403 unknown-host`. This
  stops DNS rebinding. Over plain http a browser sends no `Sec-Fetch-Site` and a same-origin
  `GET` no `Origin`, so the check cannot tell a browser apart and applies to every client.
  Add the names you browse to Copycast by, and the service names clients on a compose or
  Kubernetes network use;
- an unsafe method (anything but `GET`, `HEAD`, `OPTIONS`) whose `Sec-Fetch-Site` is neither
  `same-origin` nor `none`, or, without that header, whose `Origin` is neither `base_url`'s
  origin nor the request's own: `403 cross-site-request`. This applies to `/api`, `/uploads`,
  `/mcp` and `/feeds` alike.

Clients that send neither header (curl, podcast apps, Claude Code and other desktop or CLI
MCP clients, probes) are never refused as cross-site; like browsers, they must address
Copycast by one of its names. A browser-based MCP client on another origin, such as
MCP Inspector in a browser tab, is refused on `/mcp`. Each refusal logs `http.cross_site_refused`
with the method, the path and the reason
([ADR 0025](adr/0025-the-browser-boundary.md)).

**Security headers.**

| Where | Headers |
|---|---|
| every response | `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` |
| the web UI, `/api`, `/uploads`, `/mcp`, `/healthz` (everything but `/feeds/` and `/art/`) | `Content-Security-Policy` (below), `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`, and `Strict-Transport-Security: max-age=31536000` when `base_url` is https |
| `/api/docs` | as above, with a policy that lets Swagger UI load from `https://cdn.jsdelivr.net` and boot inline |
| `/feeds/`, `/art/`, except audio, video and PDF | `Content-Security-Policy: sandbox`: a mirrored HTML transcript, an SVG or a feed's XML opened in a browser runs no script on Copycast's origin; podcast apps ignore it. PDF Attachments are left out because a sandboxed document cannot load Chromium's PDF viewer |

The web UI's policy, with `base_url` `https://copycast.example`:

```
default-src 'self'; script-src 'self' 'sha256-XZ7u7Kv4d6fvgp7L9MVTGFXnly56yiM72Fsf3EqvlFw=';
style-src 'self' 'unsafe-inline'; img-src 'self' https://copycast.example data: https: http:;
media-src 'self' https://copycast.example; connect-src 'self' https://copycast.example;
font-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'
```

The hash is the inline theme script of `web/index.html`. Styles may be inline because Radix
and sonner inject `<style>` elements, and images may come from anywhere because a Catalog item
that is not archived shows its Source's Artwork, some of it over plain http. A header a route
sets itself wins.

**Problem types** new or changed in 2.3.0 (`application/problem+json` with `type`
`urn:copycast:problem:<slug>`; MCP tools end their error with `[<slug>]`):

| Status | Slug | When |
|---|---|---|
| 403 | `cross-site-request` | an unsafe browser request from another site |
| 403 | `unknown-host` | a request (anywhere but `/healthz`) to a host name that is not this Copycast's |
| 422 | `destination-not-allowed` | a Source URL, or a link it leads to, that the fetch guard refuses |
| 422 | `engine-option-rejected` | a Mirror's `engine_options` key off the allowlist; `keys` names each one, `scope` is `feed` (the slug is older; before 2.3.0 it refused only a deny-list) |

## Upgrading to 2.3.0

2.3.0 is the security pass (ADRs 0022 to 0026). Migration 0016 runs when the api starts and
adds the `instance_secrets` table. Nothing else in Postgres or the Object store changes.

**What podcast apps see, once.**

- Artwork URLs move to `/art/…`. Apps refetch the images and download no audio again: no
  enclosure URL changes.
- Every feed's `Last-Modified` and `lastBuildDate` move forward once, to 2026-11-05, so an
  app that revalidates with `If-Modified-Since` alone gets the new body instead of a 304.
- Every feed carries the directory block tags. A feed that Apple Podcasts or Podcast Index
  listed drops out of it.
- While authentication is on, `/feeds/<id>/assets/*.artwork.*` needs the pair. Anything that
  kept such a URL takes the `/art/` one from the feed or the API.
- Every 401 under `/feeds` has the one detail "this feed requires its username and
  password". Monitors that matched "wrong media key" or "wrong username or password for this
  feed" need the new text.

**What the operator checks.**

1. **Sources on your own network.** A Mirror whose Source is a NAS, a self-hosted podcast
   server, a `.lan` name, a compose service or a private address starts failing its Refreshes
   with "… is not an allowed destination". List those hosts in
   `[security] allow_private_hosts` before the upgrade, or afterwards from the
   `fetch.destination_refused` log lines.
2. **Environment proxies.** Copycast's own fetches ignore `HTTP_PROXY` and `HTTPS_PROXY`
   now and connect directly; there is no setting that gives them a proxy. The Engine still
   uses `proxy` from `[engine.options]`, or else those variables from the worker's
   environment. Either proxy, on a private address, must be listed in
   `[security] allow_private_hosts`, and destinations behind it are judged as well
   ("Fetch guard and browser defences" above).
3. **Per-Mirror engine options.** List the Mirrors that have any:

   ```bash
   docker compose exec postgres psql -U copycast -d copycast \
     -c "SELECT id, engine_options FROM feeds WHERE engine_options <> '{}'"
   ```

   A key off the allowlist (above, "Configuration") is ignored from now on, and each engine
   call for that feed logs `engine_options.refused feed_id=… keys=[…]`. Move cookies, a proxy
   or a PO token to `[engine.options]`, then clear the row. The Settings dialog and the API
   show only the allowed keys, so the dialog has nothing to remove, and saving it unchanged
   sends no options and leaves the row as it is (editing any option there does rewrite the
   row with the allowed set). Send the allowed set, or `{}`, yourself:

   ```bash
   curl -u copycast:"$COPYCAST_AUTH_PASSWORD" -X PATCH -H 'Content-Type: application/json' \
     -d '{"engine_options": {}}' https://copycast.example/api/mirrors/<id>
   ```

   (curl sends no `Origin`, so the cross-site guard lets it through), or in Postgres:
   `UPDATE feeds SET engine_options = '{}' WHERE id = '<id>'`.
4. **Behind a reverse proxy**, set `COPYCAST_FORWARDED_ALLOW_IPS` to the proxy's network,
   for example `docker network inspect <network> -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}'`
   for the Traefik network the api shares. Without it the request log shows the proxy's
   address as `client_ip`. The Tailscale profile needs nothing: its sidecar proxies from
   loopback. On Kubernetes, uncomment the variable in `api-deployment.yaml` with the pod CIDR.
5. **Other host names.** If browsers reach the UI, or clients reach the api, by a name other
   than `base_url`'s (a MagicDNS short name, a LAN alias, a compose or Kubernetes service name
   such as `copycast-api`), list it in `COPYCAST_ALLOWED_HOSTS`, or they get
   `403 unknown-host`. IP addresses, `localhost` and `/healthz` always pass.
6. **Browser-based MCP clients** on another origin are refused (`403 cross-site-request`).
   Claude Code and other desktop or CLI clients are not affected.
7. **A compose file of your own** (production's, say) reaches the containers only with the
   variables its services name. Add both new ones to the `environment:` of the api (or
   `api-direct`) and of the worker, next to the others:

   ```yaml
   COPYCAST_FORWARDED_ALLOW_IPS: ${COPYCAST_FORWARDED_ALLOW_IPS:-}
   COPYCAST_ALLOWED_HOSTS: ${COPYCAST_ALLOWED_HOSTS:-}
   ```

   or load `.env` with `env_file:`. Otherwise setting them in `.env` does nothing and nothing
   says so. Check with `docker compose exec api env | grep -E 'COPYCAST_(FORWARDED|ALLOWED)'`
   (`api-direct` for the direct profile).

**SeaweedFS in a compose file of your own.** The repository's `docker-compose.yml` already has
these changes. A production compose file kept elsewhere needs them by hand (adapt the service
names; [ADR 0026](adr/0026-seaweedfs-internal-apis-on-loopback-and-the-objects-network.md)):

1. Copy the new `deploy/compose/seaweedfs/entrypoint.sh` over the one the `seaweedfs` service
   mounts at `/copycast/entrypoint.sh`. It binds master, volume and filer to `127.0.0.1`, the
   S3 gateway (8333 and its gRPC, 18333) to the container network, turns the Iceberg and Lance
   listeners off and the telemetry with them.
2. In the `seaweedfs` service, delete any `ports:` and `expose:` entries and add
   `networks: [objects]`.
3. Add `networks: [default, objects]` to the service whose network namespace the api uses
   (the `tailscale` sidecar, since `api` has `network_mode: service:tailscale`), or to
   `api-direct`, and to `worker`. If a service already lists networks (a Traefik network, say),
   add `objects` to its list and keep the others. Never add it to Postgres or Valkey.
4. At the top level, keeping any networks already there (Traefik's external one):

   ```yaml
   networks:
     default:
     objects:
       internal: true
   ```

5. Apply with a plain `docker compose up -d`. Whenever the `tailscale` sidecar is recreated,
   recreate the api with it: `--force-recreate tailscale` alone leaves the api in the old,
   deleted namespace with no network. If you force a recreate, name both:
   `docker compose up -d --force-recreate tailscale api seaweedfs worker`. The first start
   takes about 10 s longer while the master re-elects itself on 127.0.0.1. The data volume needs
   no migration.

Then verify, with `<project>` the compose project name (`docker network ls` shows
`<project>_objects`):

```bash
docker inspect -f '{{json .HostConfig.PortBindings}}' $(docker compose ps -q seaweedfs)   # {}
docker compose exec seaweedfs netstat -tlnp
# 127.0.0.1 for 9333, 19333, 8080, 18080, 8888, 18888; only weed's 8333 and 18333 beyond it
docker run --rm --network <project>_objects curlimages/curl:8.17.0 \
  -sS -o /dev/null -w '%{http_code}\n' http://seaweedfs:8333/                            # 403
docker run --rm --network <project>_objects curlimages/curl:8.17.0 -sS -m 3 http://seaweedfs:8888/
# Failed to connect (the same for :9333 and :8080)
docker run --rm --network <project>_default curlimages/curl:8.17.0 -sS -m 3 http://seaweedfs:8333/
# Could not resolve host
curl -s https://<host>/healthz/ready                      # 200, checks.storage.ok is true
docker compose exec worker python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8081/healthz/ready').read().decode())"
```

From another machine, `nmap -p 8333,18333,9333,8080,8888 <host>` shows them closed or
filtered (8080 may be `api-direct`'s own published port). The host itself can still reach
18333 at the container's address, like anything on `objects`. gRPC mTLS is the follow-up
that closes it.

**Traefik's access log.** Traefik logs every request before Copycast sees it, and its
`RequestPath` field holds media keys (`/feeds/<id>/k/<key>/…`) and upload tokens. Either drop
the field (`accessLog.fields.names.RequestPath: drop` in the static configuration, which
Traefik applies to every router, or switch the copycast router's access log off where your
Traefik release offers router-level observability options), or keep the file at mode 0600 and
rotate it. Either way, rotate out the existing file once: it holds every key since 2.0.2.

**After a rebuild onto an empty database** (`copycast rebuild`), the api mints a new instance
secret, so every `/art/` URL moves once more (images only). Restart the api afterwards: a
running api keeps the secret it read, and a replica started later would read the new one.

## Mirror modes

A Mirror's Settings dialog (the *Settings* button on its page) and the Settings page offer two
policies as tabs, Ephemeral and Archive; Archive's *Backfill* switch picks between its two modes:

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

A **Locked** Episode (the padlock on its Catalog row or on any of its files in its bucket,
`PUT /api/feeds/{id}/items/{item}/archive-flag`, the `set_archive_flag` tool; `archive_flag` is
the API's name for the Lock) is never deleted: Retention, a Rolling window, Sync, a Purge of its
bucket and the admin purge skip it, and `delete_item` answers 409 (the UI disables its Delete
button). Only deleting the whole Feed removes it. A Mirror on the Archive policy Locks every
Episode it archives, and switching a Mirror to Archive (or turning Sync off on it) Locks the
Episodes it already holds; switching away unlocks nothing. A Mirror with Sync, every captured
playlist included, is never Locked automatically, so Sync keeps deleting what the Source drops
([ADR 0021](docs/adr/0021-every-feed-has-a-bucket-and-locks.md)).

Switching modes previews the change (`POST /api/mirrors/{id}/preview`) and asks for
confirmation when archived Episodes would be deleted; rolled-out and expired Episodes leave
Tombstones and can be archived again on purpose. An Automatic feed keeps listing expired and
deleted Episodes and downloads them again when a podcast app asks. The Mirror header menu
offers *Archive all Available…* (queues everything not archived yet, Shorts and Tombstones
included, with the option to switch the Mirror to Everything) and *Retry N failed*; the same
actions are the `archive_available` and `retry_failed` tools and routes. Over MCP, `create_mirror` and
`update_mirror` refuse Rolling and expiring Automatic modes, and `engine_options`, without a
`full` key. Latest N
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
  as Available if re-added). The *Playlists* dropdown next to the *Mirror* button lists the
  signed-in account's playlists through the engine when it opens (`GET /api/youtube/playlists`,
  the cookie file required, Watch Later first) and captures any of them with a click: a synced
  Mirror flagged `playlist_capture`, listed in the Feeds table like any other Feed. Agents get
  the same through `list_youtube_playlists` and `create_mirror`.
- **The Feeds page** (`/`) starts with the bar (*Mirror*, *Playlists*) and lists every Feed in
  one table: Mirrors and captured playlists with their service, Local feeds with the service
  *Local*; each row has Copy feed URL, Refresh and Pause where they apply, and Delete (never
  for the default Local feed). Under the table, a collapsed *Buckets* box opens on one card per
  Feed: its bucket, with the number of files and their size in MB (`GET /api/storage`, the
  `storage_summary` tool, one listing of the Object store, made only while the box is open).
  `/mirrors` and `/buckets` redirect to `/`, and so do the `/inboxes` links from before ADR 0018.
- **A feed's page** (`/feed/{id}`; `/mirrors/{id}` redirects there; `/feeds/` stays the public
  feed routes) shows its Catalog with a title filter, a padlock and a Delete button per Episode.
  A Mirror's header has *Files* (its bucket), *Settings* (a dialog holding the policy, the
  Source URL, the overrides and the Danger zone), *Refresh now* and *Pause*. A Local feed's
  header has its feed URL, *Upload files…*, *Files*, *Settings* (name, autoprune, Danger zone)
  and the *Add URL* form.
- **A bucket's page** (`/buckets/{id}`) shows the number of files and their total size under the
  title, *Open feed* and *Purge…* at the top right, then every file the Feed keeps (the
  published feed, media, artwork, chapters, transcripts, attachments, the descriptor, the
  Source copy, sidecars) with its kind, size, a padlock and a download link
  (`GET /api/feeds/{id}/objects`, the `list_feed_objects` tool;
  `GET /api/feeds/{id}/objects/{path}` downloads one file). The padlock is the Lock of the
  archived Episode the file belongs to; files a Purge never deletes (the Feed's own files, the
  `.item.xml` records, leftovers of items not archived) show a disabled one. *Purge…* runs a dry
  run of `clear_feed`, says how many files are Locked and stay, and on *Yes* deletes every
  archived Episode that is not Locked.
- **Uploading files into a Local feed**: *Upload files…* on its page takes mp3, m4a and m4b
  files, sends them one by one in natural file-name order (Part 2 before Part 10) with a
  progress bar each, then adds them as archived Episodes dated by arrival, one second apart
  (`create_upload`, the `PUT` to its `upload_url`, then `add_uploads`). Agents use the same
  three steps; `create_serial_bucket` stays the way to publish a work in an order they choose.
- **Adding a Source** happens in a dialog from the Feeds page bar (a URL, an Apple Podcasts or
  Spotify link, or a name to search) or the command palette; the Mirror takes the operator's
  default policy, changed afterwards in its Settings dialog.

## Show notes, OPML and the player

- **Attachments.** When an Episode is archived, the images its show notes embed and the
  files they link to (images, PDFs, audio; up to 20 per Episode, 5 MiB per image, 20 MiB per
  file) are mirrored as `attachment` assets under `assets/{item}.attachment.{slot}.{ext}`,
  and the Mirror Feed's description points at the local copies. A file that cannot be fetched
  is recorded as a failed asset and its original URL stays in the notes.
- **OPML.** *Export OPML* on the Settings page (`GET /api/feeds.opml`) lists every feed with
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

- A Mirror's Settings dialog has a *Title* field: a title of your own is shown in the UI, the
  Mirror Feed and MCP and survives Refreshes (`title_override`; the Source's title stays in
  `source_title`); *Use Source title* clears it.
- Artwork (`/art/<asset_id>-<mac>.<ext>`) and the Copycast logo
  (`/feeds/copycast-artwork.png`, the artwork of a Local feed without its own) answer without
  credentials even while authentication is on: podcast apps fetch images bare, and the MAC
  keeps the URLs unguessable (see "Authentication"). Media, feeds, chapters and transcripts
  keep the feed's pair.
- Every feed asks directories not to list it: its channel carries
  `<itunes:block>Yes</itunes:block>` and `<podcast:block>yes</podcast:block>`, replacing any
  the Source sets. Apple Podcasts and Podcast Index honour them; your own podcast app ignores
  them.
- `/robots.txt` disallows everything and every response carries
  `X-Robots-Tag: noindex, nofollow, noarchive`; the UI shell adds the equivalent meta tag.
- `POST /api/admin/purge` (`dry_run` true by default) deletes every archived Episode of every
  feed except the Locked ones; feeds, Catalogs, artwork and credentials stay, and the Episodes
  leave Tombstones. It has no button in the UI (the About page shows versions and totals only).

## Settings page: Authentications, Mirror defaults, OPML

The Settings page saves every change as it is made, with no Save button. *Authentications*
shows YouTube, Twitch, Vimeo and Mixcloud with a green check once a stored cookie file covers
them (see below). *Export OPML* downloads every feed for a podcast app. Each Feed's own settings
live on its page, and its files on its bucket's page.

The *Mirrors* section holds defaults that live in the Object store (`engine/defaults.json`,
`PUT /api/settings/defaults`) rather than in the environment, because they are policy the
operator changes from the UI:

| Default | Effect | Per-Mirror override |
|---|---|---|
| Metadata language (`fr`, `pt-BR`) | yt-dlp asks YouTube for that language instead of English, so a French channel's titles and descriptions stay French | the Mirror's *Metadata language* field (`preferred_language`; `language` stays what the Source reports) |
| Minimum length (minutes) | items shorter than that are listed but never archived by Backfill or Follow; unknown lengths pass; explicit selections still work (keeps Shorts out) | the Mirror's *Minimum length* field (`min_duration_seconds`) |
| Policy | the policy a new Mirror starts from: Ephemeral (with its retention) or Archive (with or without Backfill) | the Mirror's Settings dialog, or `backfill` in `create_mirror` |
| Refresh every (hours) | how often the scheduler Refreshes a Mirror (24 by default; replaces the `refresh.interval_hours` config key) | the Mirror's *Refresh every* field (`refresh_interval_hours`) |

A Mirror's Settings dialog shows "Using the default" when a field is empty, marks a value that
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
  The ingress proxies from another pod: set `COPYCAST_FORWARDED_ALLOW_IPS` to the cluster's
  pod CIDR (commented out in `api-deployment.yaml`; k3s uses `10.42.0.0/16`) so `client_ip`
  and the scheme are the client's.

The worker is active-passive by construction (one runs at a time); the api scales out.
Nothing is pinned to a node: the archive lives in the Object store.

## Backup and restore

Back up the Object store (for SeaweedFS in compose, the `seaweedfs` volume or the
`COPYCAST_S3_DATA` directory while it is stopped, or any S3 sync tool against the bucket). It
is sufficient: media, sidecars, original Source XML, assets and the `feed.json` descriptors
reconstruct the database. In compose, SeaweedFS is reachable only on the internal `objects`
network, so a sync tool runs there (`docker run --rm --network <project>_objects …
http://seaweedfs:8333`). To reach an off-site target (B2, S3, an rclone remote) it also needs
`<project>_default` (`docker network connect <project>_default <container>`), since `objects`
has no egress. The Copycast image ships no S3 sync tool. Optionally add a `pg_dump` to keep
telemetry (job history, refresh runs, download counters, engine versions) and the instance
secret that `/art/` URLs are keyed with (without it a restore mints a new one and image URLs
move once):

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

Lost by a rebuild: jobs and their logs, refresh runs, heartbeats, engine version history,
download counters and the instance secret. Everything else, including item ids and ordinals,
comes back identical. Onto an empty database the api mints a new secret, so every Artwork URL
(`/art/…`) changes once; podcast apps refetch the images, never the audio. Restart the api
after such a rebuild: a running api keeps the secret it read.

## Engine bumps and rollback

The engine (yt-dlp) is pinned in `uv.lock`. `engine-bump.yml` runs every two weeks (the 1st
and 15th of the month, or on demand from the Actions tab), pins the newest release of the
configured channel (`vars.ENGINE_CHANNEL`, nightly by default), runs the engine tests, and
pushes the lock change to `main`; `image.yml` then builds and publishes:

| Tag | Moves? | Use |
|---|---|---|
| `latest` | yes | the newest successful build |
| `2.3.0` | yes, on every engine bump | "the current 2.3.0" |
| `2.3.0-yt2026.10.29.232701.dev0` | never (until pruned) | exactly this app + engine combination |
| `sha-<short>` | never | one commit |

Every image is smoke-tested (`scripts/smoke.sh`) before its tags move. After each build the
package is pruned to the three newest tagged images; older images and every untagged manifest
are deleted, so roll back within that window or pull and keep the image yourself. The prune
runs with `GITHUB_TOKEN`, which needs the package to grant the repository the Admin role
(Package settings → Manage Actions access). To roll back an engine that broke a site, pin
the previous immutable tag in `.env`
(`COPYCAST_IMAGE=ghcr.io/flrntdfr/copycast:2.3.0-yt<previous>`) or in the kustomize overlay
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
`content_length` (the response header, when set), `client_ip` (the address uvicorn resolved:
the socket peer, or the `X-Forwarded-For` hop a proxy listed in `COPYCAST_FORWARDED_ALLOW_IPS`
named), `forwarded_for` (the raw header as the client sent it, which anyone can forge),
`user_agent`, `range`, `request_id` and `completed` (false when the response never finished:
an unhandled exception is logged as `status=500`, a client that went away with the status it
got). Requests under `/healthz/` are never logged; `Authorization` and usernames never appear.
A fetch that ran a Light Refresh also logs one `light_refresh.done` line (`feed_id`, `status`,
`kind`, `new`, `wanted`, `enqueued`, `duration_ms`).

No line carries a secret, whatever logged it (Copycast, uvicorn, httpx, yt-dlp), in JSON or
console form, tracebacks included. URL userinfo becomes `scheme://***@host`, media keys
`/k/***/`, upload tokens `/uploads/<id>/***`, API keys `cck_XXXXXXXX***`, and `Authorization`
and `Cookie` values and Basic, Bearer and Digest credentials `***`. Console tracebacks show no
frame locals. The security events are `fetch.destination_refused` (a Source fetch the guard
refused, with the address and its class), `http.cross_site_refused` (method, path, reason)
and `engine_options.refused` (a stored Mirror option the allowlist dropped).

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
