# Copycast 🐱

Mirror and archive podcasts. Paste a URL — a podcast RSS feed, a YouTube
channel or playlist, or anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp)
supports — and Copycast keeps a local audio copy with artwork and metadata,
and publishes its own RSS feed for your podcast app. **Your feed keeps
working even if the original disappears.**

## Run it

Prebuilt images (amd64, arm64) are on the GitHub container registry:

```bash
docker compose up -d
# or
docker run -d -p 8080:8080 -v ./data:/data -v ./config:/config \
  ghcr.io/flrntdfr/copycast:latest
```

Set `copycast.base-url` in `config/application.yaml` to the address your
podcast app will reach the server at — it is used in the feed links.

## Use it

1. Open `http://localhost:8080`.
2. Paste a URL and click **Mirror**. Optionally archive only the latest N
   episodes instead of the full backlog.
3. Copy the Mirror Feed URL into your podcast app. Done.

Mirrors refresh automatically (daily, and whenever your podcast app polls
the feed). Episodes deleted upstream stay in your feed — that's the point.
**Pause** stops downloads (they resume where they left off); **Delete**
removes a mirror and its files for good.

## Configure it

All options live in one file, [`config/application.yaml`](config/application.yaml):

| Option                            | Default                 | Meaning                                  |
|-----------------------------------|-------------------------|------------------------------------------|
| `copycast.base-url`               | `http://localhost:8080` | Public address used in feed links        |
| `copycast.refresh-hours`          | `24`                    | How often mirrors refresh                |
| `copycast.fetch-cooldown-minutes` | `15`                    | Throttle for poll-triggered refreshes    |
| `copycast.ytdlp.version`          | pinned release          | yt-dlp version — bump it, then restart   |

Everything Copycast knows lives in plain files under `/data` — no database.
Back up that folder and you've backed up everything.

## Good to know

- **No built-in authentication.** Run it on a private network (LAN, VPN,
  Tailscale) or behind your own reverse proxy.
- Audio is never re-encoded: podcast files are archived byte-identical.
- When a site change breaks downloads, bump `copycast.ytdlp.version` in the
  config and restart — no image rebuild needed.

## Development

Java 21+, Maven and ffmpeg — or just `direnv allow` with Nix. `make help`
lists the targets (`serve`, `test`, `build`, docker). Design notes live in
[CONTEXT.md](CONTEXT.md) and [docs/adr/](docs/adr/).
