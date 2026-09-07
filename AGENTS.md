# Copycast

A self-hosted podcast mirroring and archiving service. You point it at a Source of audio content (a podcast feed, a page advertising feeds, a YouTube channel or playlist, anything the Engine supports); it keeps a durable local copy and publishes its own podcast feed that keeps working even if the original disappears. Agents get the same capabilities as the web UI through an MCP server: find a podcast by name and mirror it, archive an exact selection of episodes, or push arbitrary URLs into an Inbox feed.

Copycast v1 is a Python (FastAPI, SQLAlchemy, Postgres) modular monolith with yt-dlp imported as a library in a worker process, a React frontend, and an MCP server sharing the API's service layer. It ships as one container image run as two processes.

## Language

Use these terms, in code and in conversation. The _Avoid_ lists name the words that mean something else here or that blur a distinction.

**Feed**:
The umbrella for the two kinds of thing Copycast publishes a podcast feed for: a Mirror or an Inbox. Every Feed has an id, a title, a Catalog, a data directory and a published RSS document.
_Avoid_: Channel, show, podcast (for the thing Copycast serves)

**Mirror**:
A Feed that tracks one Source. Created once, then kept up to date automatically; it continues serving its archived content even when the Source becomes unreachable. Copycast never deletes anything from a Mirror on its own.
_Avoid_: Archive, snapshot, copy, subscription

**Source**:
The external URL a Mirror tracks — a podcast RSS feed, a YouTube channel or playlist, or anything else the Engine supports. Compared for duplicates through a normalized dedup key; fetched with the URL exactly as given.
_Avoid_: Original, upstream, target

**Mirror Feed**:
The RSS feed Copycast publishes for a Mirror — the thing you subscribe to in a podcast client. It preserves the Source's original metadata but points at archived audio and assets.
_Avoid_: Output feed, generated feed, proxy feed

**Inbox**:
A Feed with no Source. It is filled by Requests — arbitrary URLs pushed by a person or an agent — and is the only kind of Feed with Retention. One default Inbox named "Copycast" always exists: it can be renamed but never deleted.
_Avoid_: Queue, dropbox, watch later, bucket

**Inbox Feed**:
The RSS feed Copycast publishes for an Inbox: the same shape as a Mirror Feed, with every item synthesized from Engine metadata and ordered by the time it was added.
_Avoid_: Inbox RSS, request feed

**Request**:
One URL pushed into an Inbox, recorded with who pushed it (UI or MCP) and what it expanded into. A Request for a page or playlist expands to many Catalog items; a Request for a podcast feed is refused ("use create_mirror").
_Avoid_: Submission, task, job (that is the worker's word)

**Episode**:
A Catalog item with archived media; its Mirror Feed entry is captured once at archive time and never rewritten from the Source afterwards. The Mirror Feed lists every Episode ever archived that has not been deleted by you.
_Avoid_: Item (say Catalog item when it may not be archived), entry, track, video

**Artwork**:
The cover image of a Feed and the images of its Episodes, archived alongside the audio and served from the Feed like any other asset.
_Avoid_: Thumbnail, image

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
A Feed's permanent, opaque identity. For a Mirror it is derived from the Source's dedup key at creation; for an Inbox from its name plus a random suffix. It names the feed URL and the data directory and never changes — podcast clients depend on it.
_Avoid_: Slug, name, feed name

**Backfill**:
The part of a Mirror's policy that says which items get archived: everything, a Rolling window, on demand (Automatic), or an explicit selection (Latest N remains for Mirrors that already use it). Everything and Latest N apply once, at the first Refresh (immediately for a selection).
_Avoid_: Initial sync, history, catch-up, mode (in prose; the UI shows the modes as tabs)

**Rolling**:
The Backfill mode that keeps exactly the newest N items by publication archived: applied at every Refresh and at once when chosen; archived items that fall outside the window are tombstoned (ADR 0012).
_Avoid_: Sliding window, keep-last, FIFO

**Automatic**:
The Backfill mode that archives nothing ahead of time: the Mirror Feed lists every item the Source lists, an Episode is archived when a podcast app first asks for its media (the request waits up to two minutes, then says retry), and it expires after the Mirror's Retention.
_Avoid_: Lazy, on-demand mode (say "archived on demand" for the act), streaming

**Follow**:
The part of a Mirror's policy that says whether items the Source lists after creation are archived automatically. Off by default when the Backfill is a selection.
_Avoid_: Auto-download, subscribe, track (that is what a Mirror does with a Source)

**Refresh**:
The recurring act of checking a Source for new items and archiving what the policy wants. Triggered by schedule, by a manual action, or by a fetch of the Mirror Feed (only when Following, not Paused, and outside a cooldown; asynchronously — serving the feed is never delayed). A failed Refresh never degrades the Mirror Feed; the Mirror keeps serving what it has.
_Avoid_: Sync, update, poll

**Paused**:
The state of a Mirror that no longer Refreshes but keeps serving its Mirror Feed and archived Episodes. The only way to stop a Mirror without destroying its archive.
_Avoid_: Disabled, stopped, archived

**Retention**:
An Inbox's rule for deleting Episodes it no longer needs: automatically N days after their first download, or on demand by criterion; for an Automatic Mirror, the days after an Episode's last download before it is tombstoned (7 by default, none keeps forever). Other Mirrors have no Retention. Inbox Episodes never downloaded are never auto-pruned.
_Avoid_: Expiry, TTL, cleanup, garbage collection

**Download count**:
How many times an Episode's media was fetched by a client: a full GET or a Range request starting at byte 0 counts once; HEAD requests and later ranges never count. Drives Retention and is telemetry, not intent.
_Avoid_: Plays, listens, hits

**Tombstone**:
The trace left by an Episode you deleted (or that rolled out, expired, or was purged): the Catalog item stays, marked deleted, so the Mirror will not archive it again on its own; it shows as Available and can be re-archived on demand, and an Automatic Mirror downloads it again when a podcast app asks.

**Title override**:
A Mirror's own title, kept over the Source's across Refreshes and shown in the UI, the Mirror Feed and MCP; clearing it restores the Source's title.
_Avoid_: Rename (that is an Inbox), alias, custom name
_Avoid_: Blacklist, ignore list, ban

**Engine**:
yt-dlp as used by Copycast: imported as a library, lockfile-pinned to one version (nightly channel by default), bumped by automation. Every download goes through it, RSS enclosures included; its version is recorded on every archived item and shown on the About page.
_Avoid_: Downloader, backend, scraper, yt-dlp binary

**Operator password**:
The one credential from the environment (`COPYCAST__AUTH__PASSWORD`, username `copycast` unless `COPYCAST__AUTH__USERNAME` says otherwise) that opens the web UI, the API and every feed over HTTP Basic. Setting it switches authentication on; unset, Copycast is open and the network is the boundary (ADR 0004, ADR 0011).
_Avoid_: Admin password, login, account, session

**Feed credentials**:
A Feed's own random username and password, minted at creation, kept in Postgres and in `feed.json`, and rotated on demand. While authentication is on they open that Feed's RSS, media and assets and nothing else; the feed URL carries them as `user:pass@` and the UI shows them separately for apps such as Overcast.
_Avoid_: Feed token, feed secret, private URL, feed password (say the pair)

**API key**:
A bearer secret minted from the UI for one MCP client, shown once, stored as a digest, revocable on its own. The only credential the MCP mount accepts while authentication is on.
_Avoid_: Token, PAT, MCP password, service account

**Default**:
An operator setting on the Settings page (metadata language, minimum length) that every Mirror inherits unless its own value is set; stored in `data/engine/defaults.json`. A Mirror overrides it by setting a value and resets by clearing it.
_Avoid_: Global setting, preference, config (that is the environment)

**Minimum length**:
A Mirror's rule, or the Default, that keeps items shorter than N seconds Available: never archived by Backfill or Follow, still archivable by an explicit selection. Unknown lengths pass. Keeps YouTube Shorts out.
_Avoid_: Filter, Shorts rule, duration threshold

**Cookie file**:
The one Netscape `cookies.txt` the Engine carries along on every listing and fetch, stored at `data/engine/cookies.txt` from the Settings page so sites that demand a login (YouTube from a server's IP) accept downloads. Never shown back, never exposed to agents.
_Avoid_: Cookies setting, session file, YouTube login

**Scope**:
What an API key may do over MCP: `read` (read-only tools), `write` (everything but deletions) or `full` (everything). Every tool checks it before running.
_Avoid_: Role, permission, level, grant

## Conventions for agents

### Stack

Python 3.13, uv lockfile, FastAPI, Pydantic v2, pydantic-settings (`config/copycast.toml` plus `COPYCAST__SECTION__KEY` environment variables; `COPYCAST__AUTH__PASSWORD` switches authentication on), SQLAlchemy 2 async with psycopg 3, Alembic, structlog, httpx, lxml, sse-starlette, fastmcp, click; `yt-dlp[default]` from the lockfile; ffmpeg from the OS image; Postgres 17. Frontend: React 19, TypeScript, Vite 7, Tailwind v4, shadcn/ui, TanStack Router/Query/Table, openapi-typescript + openapi-fetch, vitest + MSW, Playwright; package manager pnpm 11 via corepack (never npm or yarn). Delivery: one image, Docker Compose (Tailscale sidecar or direct port), Kubernetes kustomize base, GitHub Actions.

### Repository map

```
src/copycast/domain/         pure rules: enums, ids, urls, listing, ordinals, selection, engine_options
src/copycast/application/    models (the API/MCP contract), capabilities registry, ports (Engine), events, services/
src/copycast/adapters/       api (FastAPI), mcp (fastmcp), db (SQLAlchemy, migrations), storage (layout, descriptor,
                             rebuild), engine (the only importer of yt_dlp), sources (RSS, discovery, probe, iTunes),
                             feeds (RSS rendering), assets (Artwork, chapters, transcripts)
src/copycast/worker/         runner, scheduler, progress, joblog, jobs/{refresh,archive_item,expand_request,prune,rebuild}
src/copycast/{app,cli,settings,logging,version}.py   composition root, click CLI, configuration
web/                         Vite app; web/openapi.json is committed and generated into src/api/schema.d.ts
deploy/                      compose/docker-compose.dev.yml (Postgres for development), kubernetes/{base,overlays/example}
tests/                       unit, integration/{storage,worker,api,mcp,engine}, e2e, support (FakeEngine, Origin), fixtures
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

1. Add the name to `CAPABILITY_NAMES` and, if destructive, to `DESTRUCTIVE` (`application/capabilities.py`); add request/response models to `application/models.py`.
2. Implement the service function under `application/services/` decorated with `@capability(...)`; it receives a UnitOfWork and raises domain exceptions (`NotFound`, `Conflict`, `Unsupported`, ...) which the API maps to problem+json and MCP to `ToolError`.
3. Add the route in `adapters/api/` with `operation_id` and `x-capability`; add the tool in `adapters/mcp/` with `meta={"capability": name}` (or add the name to `TOOL_EXEMPT` / `INTERNAL` with a reason).
4. `make openapi`, then register the operation in `web/src/api/ops.ts` and build the screen.
5. Tests: a unit test for the service with `FakeEngine`, an API test with the `client` fixture, an MCP test with `mcp_client`; the convergence test picks the rest up.
6. Update the Language section here when the capability introduces a term, and write an ADR when it changes a decision.

### Dependency pinning

Everything is pinned exactly (Python `==` in pyproject.toml, exact versions in web/package.json, Docker images by digest, Actions by commit SHA, nixpkgs by flake.lock). Bump with `uv add "name==x.y.z"` or `pnpm --dir web add name@x.y.z`, never by loosening a pin; Dependabot opens the upgrade PRs. yt-dlp is moved only by `engine-bump.yml`. See docs/operations.md "Dependency pinning".

### Testing rules

- The Engine is never real in unit or integration tests: use `tests/support/fake_engine.py` (`script_listing`, `fail_next`, `block_fetches`). Real yt-dlp runs only in `tests/integration/engine` (`ffmpeg` marker, `network` marker gated by `COPYCAST_TEST_NETWORK=1`).
- Postgres tests are marked `integration` and get a per-test database cloned from a migrated template (`db` fixture); never share state between tests. Set `COPYCAST_TEST_DATABASE_URL` to skip testcontainers.
- HTTP Sources come from `tests/support/origin.py` serving `tests/fixtures` (Range, ETag, scripted 304/5xx); never reach the internet in tests.
- Rendered feeds are compared canonically (`tests/support/xml.py`) against golden files; regenerate with `make golden-regen` and review the diff.
- Keep coverage above the gates (85 % overall, 95 % for `copycast.domain`).

### ADR index

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-postgres-is-truth-data-dir-rebuildable.md) | Postgres is the runtime truth; the data directory is complete enough to rebuild it |
| [0002](docs/adr/0002-engine-in-process-lockfile-pinned-nightly.md) | yt-dlp runs in process, pinned by the lockfile, nightly channel by default |
| [0003](docs/adr/0003-one-contract-ui-api-mcp.md) | UI, API and MCP share one capability contract |
| [0004](docs/adr/0004-network-is-the-security-boundary.md) | No authentication: the network (Tailscale) is the boundary |
| [0005](docs/adr/0005-modular-monolith-two-processes-one-image.md) | Modular monolith, two processes, one image |
| [0006](docs/adr/0006-image-tags-and-engine-bump-automation.md) | Image tags and automated engine bumps |
| [0007](docs/adr/0007-inbox-and-requests.md) | Inbox feeds fed by Requests |
| [0008](docs/adr/0008-ordinals-source-numbering-selections.md) | Ordinals, Source numbering and selections |
| [0009](docs/adr/0009-never-delete-tombstones.md) | Copycast never deletes from a Mirror; user deletions leave Tombstones |
| [0010](docs/adr/0010-download-counting-rule.md) | What counts as a download |
| [0011](docs/adr/0011-optional-authentication-for-direct-deployments.md) | Optional authentication: operator password, feed credentials, MCP API keys |
| [0012](docs/adr/0012-mirror-modes-rolling-and-automatic.md) | Mirror modes: Rolling windows and Automatic archives narrow ADR 0009 |
