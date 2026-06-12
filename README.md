# Copycast

Self-hosted podcast mirroring and archiving. Paste a URL — a podcast RSS feed,
a YouTube channel or playlist, or anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp)
supports — and Copycast keeps a durable, audio-only local copy and publishes
its own RSS feed (the **Mirror Feed**) for your podcast client. The Mirror
Feed keeps working even when the original feed or its audio files disappear.

The project's domain language is defined in [CONTEXT.md](CONTEXT.md);
architectural decisions are recorded in [docs/adr/](docs/adr/).

## How it works

- A **Mirror** is a standing subscription to one **Source**. It is refreshed
  on a schedule (default: every 24 h), on demand from the UI, and whenever a
  podcast client fetches the Mirror Feed (asynchronously, at most once per
  15-minute cooldown).
- The whole backlog is archived by default; an optional per-Mirror cap
  ("latest N only") can be set at creation.
- Audio is never re-encoded when the Source already serves audio: podcast
  enclosures are archived byte-identical (`yt-dlp -x` without a forced
  format). Video sources are extracted to audio, preferring a lossless remux.
- For RSS Sources the original feed XML is stored on every refresh, and each
  item's XML fragment is captured permanently when its Episode is archived.
  The Mirror Feed is the **union of everything ever archived** with original
  metadata preserved — upstream deletions never propagate. Episodes the
  Source no longer lists are flagged as *Delisted* in the UI only.
- **Artwork is archived too**: the podcast cover and per-episode images are
  stored next to the audio and the Mirror Feed points at the local copies,
  so artwork survives the Source as well.
- There is **no database** ([ADR 0001](docs/adr/0001-filesystem-as-state.md)).
  Everything lives in plain files under the data directory — one
  self-describing folder per Mirror that you can back up with rsync.

## Quick start (Docker)

Prebuilt multi-arch images (amd64, arm64) are published to the GitHub
container registry by CI on every push to `main`, and docker-compose.yml
uses them directly:

```bash
docker compose up -d
# UI:    http://localhost:8080
# Feeds: http://localhost:8080/feed/{mirror-id}/feed.xml
```

While the repository is private, pulling requires a one-time
`docker login ghcr.io` with a token that has `read:packages` (or make the
package public in its settings). To build the image locally instead, use
`make docker-build`.

Mounts:

| Path      | Purpose                                                    |
|-----------|------------------------------------------------------------|
| `/data`   | All state: mirrors, audio, metadata, the yt-dlp binary     |
| `/config` | `application.yaml` with every option (see [config/](config/)) |

Set `copycast.base-url` in `config/application.yaml` to the URL your podcast
client will reach the server at — it is baked into enclosure links.

## Configuration

All options are consolidated in one file: [`config/application.yaml`](config/application.yaml).

| Option                            | Default                 | Meaning                                   |
|-----------------------------------|-------------------------|-------------------------------------------|
| `copycast.base-url`               | `http://localhost:8080` | Public base URL for feed/enclosure links  |
| `copycast.data-dir`               | `data` (`/data` in Docker) | Where all state lives                  |
| `copycast.refresh-hours`          | `24`                    | Scheduled refresh cadence                 |
| `copycast.fetch-cooldown-minutes` | `15`                    | Cooldown for feed-fetch-triggered refresh |
| `copycast.ytdlp.version`          | pinned release          | Exact yt-dlp version to run               |
| `copycast.ytdlp.auto-download`    | `true`                  | Fetch the pinned binary at startup        |

The pinned yt-dlp binary is baked into the image at build time — the
Dockerfile greps the version from `application.yaml`, so the pin lives in
exactly one place ([ADR 0002](docs/adr/0002-ytdlp-version-pinned-in-config.md)).
The active version and release date are shown on the main page. When a site
change breaks the extractor, bump `copycast.ytdlp.version` and either rebuild
the image or just restart: the runtime falls back to downloading a pin that
doesn't match the bundled binary into `/data/bin`.

## Security

There is no built-in authentication yet. Run Copycast on a private network
(LAN, VPN, Tailscale) or behind your own reverse proxy. The public surface a
podcast client needs is isolated under `/feed/**`; everything else is UI.
This split is deliberate so authentication can later be added in front of
the UI without ever moving feed URLs your clients are subscribed to.

## Versioning and releases

The project version lives in one place: the `<version>` element of
[`pom.xml`](pom.xml). Bump it there as development progresses. On every
push to `main`, CI runs the test suite and publishes
`ghcr.io/flrntdfr/copycast:<version>` and `:latest` for `linux/amd64` and
`linux/arm64`. Pull requests run the tests only.

## Development

Requires Java 21, Maven and `ffmpeg` on the PATH. With [Nix](https://nixos.org)
and [direnv](https://direnv.net), `direnv allow` (or `nix develop`) drops you
into a shell with everything installed.

```bash
make serve        # dev mode with hot reload at http://localhost:8080
make test         # unit tests
make build        # production jar (builds the frontend bundle)
make serve-prod   # build and run the production jar
make docker-up    # build and start via docker compose
make help         # all targets
```
