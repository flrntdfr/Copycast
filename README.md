# Copycast

Mirror and archive podcasts. Paste a URL — a podcast RSS feed, a page that advertises one,
a YouTube channel or playlist, or anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp)
supports — and Copycast keeps a local copy of the audio with artwork, chapters and
transcripts, and publishes its own podcast feed for your podcast app. **Your feed keeps
working even if the original disappears.**

Agents get the same powers through a built-in [MCP](https://modelcontextprotocol.io) server:
find a podcast by name and mirror it, archive exactly the episodes you name ("1-42, 180"),
or drop arbitrary URLs into an Inbox feed that prunes itself.

## Quick start (Docker Compose)

Prebuilt images for amd64 and arm64 are on the GitHub container registry.

```bash
git clone https://github.com/flrntdfr/copycast && cd copycast
cp .env.example .env            # choose a profile, set the base URL and the Postgres password
mkdir -p data config && chown -R 1000:1000 data
docker compose up -d
```

Copycast runs as uid 1000 inside the containers and refuses to start with a data directory
it cannot write to — the `chown` is the one step people forget. Configuration lives in
`config/copycast.toml` (optional; every key has a default and can be set through
`COPYCAST__SECTION__KEY` variables). See [docs/operations.md](docs/operations.md) for the
full environment-variable table, backups and rebuilds.

### Two ways to reach it

By default Copycast has **no authentication**: the network is the boundary
([ADR 0004](docs/adr/0004-network-is-the-security-boundary.md)). Pick one profile in `.env`:

- `COMPOSE_PROFILES=tailscale` (recommended): the api shares the network namespace of a
  Tailscale sidecar and is reachable only inside your tailnet, as
  `https://copycast.<your-tailnet>.ts.net`, with a certificate issued by Tailscale. Set
  `TS_AUTHKEY` and `COPYCAST_BASE_URL=https://copycast.<your-tailnet>.ts.net`.
- `COMPOSE_PROFILES=direct`: the api publishes port 8080 on the host, for a trusted LAN or
  behind your own reverse proxy. Set `COPYCAST_BASE_URL` to the address your podcast app
  will use — it is embedded in every feed and media link.

### Switching authentication on

For a direct deployment that is not on a trusted network, set `COPYCAST_AUTH_PASSWORD` in
`.env` ([ADR 0011](docs/adr/0011-optional-authentication-for-direct-deployments.md)). Put
TLS in front first: HTTP Basic without it is not a gate. With a password set:

- The **web UI and the API** ask for the operator pair (username `copycast` unless
  `COPYCAST_AUTH_USERNAME` says otherwise); the browser remembers it for the session.
- Every **feed gets its own username and password**, minted when it is created and shown
  next to its URL. The feed URL you copy already carries them
  (`https://user:pass@host/feeds/….xml`), which Overcast, Pocket Casts and AntennaPod
  accept for the feed and its episodes. Apple Podcasts fetches such a feed but not its audio.
  A pair opens that one feed; **Rotate** mints a new one and cuts off every client holding
  the old one.
- **MCP takes API keys** minted on the *API keys* page, each with a scope (`read`, `write`
  or `full`) that caps what the agent may do. The key is shown once. For Claude Code:

  ```bash
  claude mcp add --transport http copycast https://copycast.example/mcp --header "Authorization: Bearer $COPYCAST_MCP_KEY"
  ```

  Claude Desktop and claude.ai custom connectors need OAuth and cannot use a key.

## Use it

1. Open Copycast and click **Add a Source** (or press ⌘K and paste a URL).
2. Copycast probes the URL. A page advertising several feeds shows a candidate per feed; a
   bare YouTube channel becomes its Videos tab.
3. Choose what to archive — **Everything**, the **latest N**, or a **selection** such as
   `1-42, 180` — and whether to **Follow** the Source for new episodes.
4. Copy the feed URL into your podcast app. It answers immediately; episodes appear as they
   are archived, with live progress in the UI.

Mirrors refresh on a schedule (daily by default) and whenever your podcast app polls the feed
while Following (throttled by a cooldown). Episodes the Source drops stay in your feed —
that is the point. **Pause** stops downloads without touching the archive; **Delete** removes
a Mirror and its files for good, and is the only thing that ever deletes from a Mirror
([ADR 0009](docs/adr/0009-never-delete-tombstones.md)).

### Inboxes

An **Inbox** is a feed without a Source: you (or an agent) push URLs into it — a single
YouTube video, a SoundCloud set, a talk on a conference site — and each becomes an episode of
the Inbox feed. Inboxes can prune themselves N days after an episode's first download
(never-downloaded episodes are kept), or on demand by criterion. A default Inbox named
**Copycast** always exists.

### MCP

The MCP server is served at `<base_url>/mcp` (Streamable HTTP, stateless). Add it to a client
such as Claude Desktop or Claude Code (with authentication on, only clients that send a
static header work; see above):

```json
{
  "mcpServers": {
    "copycast": { "url": "https://copycast.<your-tailnet>.ts.net/mcp" }
  }
}
```

Tools cover everything the UI does: `search_podcasts` (iTunes Search, no key needed),
`probe_source`, `create_mirror`, `archive_episodes`, `add_to_inbox`, `prune_inbox`,
`list_feeds`, `list_items`, `refresh_mirror`, `set_mirror_paused`, `delete_feed`
(requires `confirm=true`), and the job tools. Typical exchanges:

- "Mirror Accidental Tech Podcast" → `search_podcasts` → `create_mirror` returns the feed URL
  synchronously.
- "Archive episodes 1 to 42 and 180 of that show" → `create_mirror` with
  `backfill.mode = selection` (Follow off) → exactly 43 episodes, nothing more.
- "Save this talk for my commute" → `add_to_inbox` → it lands in the Copycast Inbox feed.

## Good to know

- Audio is never re-encoded: the best audio stream (m4a preferred) is stream-copied, with
  tags and artwork embedded.
- Everything Copycast knows is either in Postgres or under `data/`; the data directory alone
  is enough to rebuild the database (`copycast rebuild`,
  [ADR 0001](docs/adr/0001-postgres-is-truth-data-dir-rebuildable.md)).
- The engine (yt-dlp) is pinned in the image and bumped automatically every six hours; every
  image is also tagged `<version>-yt<engine>` so you can roll back to a known-good engine
  ([ADR 0006](docs/adr/0006-image-tags-and-engine-bump-automation.md)).
- Kubernetes: a kustomize base with CloudNativePG and a Tailscale Ingress lives in
  [deploy/kubernetes](deploy/kubernetes); see [docs/operations.md](docs/operations.md).

## Development

Python 3.13 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm (via corepack), ffmpeg
and Docker — or `direnv allow` for the Nix dev shell.

```bash
make setup          # uv sync + pnpm install from the lockfiles
make db-up          # development Postgres
make dev            # api with reload + worker + Vite dev server
make lint typecheck test
make help           # everything else
```

The domain language every contributor and agent should use is in [AGENTS.md](AGENTS.md);
decisions are recorded in [docs/adr](docs/adr).
