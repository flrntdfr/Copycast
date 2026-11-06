# 0024 A Mirror's engine options are an allowlist, and need a full key over MCP

Status: accepted (2.3.0); amends ADR 0002 (per-Mirror options are no longer filtered by a
deny-list) and extends ADR 0011's scopes; amended by ADR 0028 (setting them is Admin-only,
and a key's scope is checked in the service, on `/api` as on MCP)

## Context

A Mirror's `engine_options` are raw `YoutubeDL` params, stored with the feed and handed to
yt-dlp on every listing and fetch. Until 2.3.0 they passed a deny-list: everything except
the options Copycast owns (output paths, format selection, postprocessors, hooks) and
`cookiefile`/`proxy`. yt-dlp has well over a hundred params and adds more, and some of the
ones the deny-list let through were dangerous. `netrc_cmd` runs a shell command (SEC-1).
`usenetrc` and `netrc_location` read files. `http_headers` can carry a `Ytdl-Request-Proxy`
header, which yt-dlp takes as the request's proxy. `username` and `password` carry identities.
Any write credential could set them: with authentication off, anyone on the network; with it
on, the operator pair or a `write` API key.

## Decision

**Two validations, kept apart** (`domain.engine_options`):

- `[engine.options]` in the configuration file is the operator's. Any param passes except
  the engine-owned ones (`ENGINE_OWNED_OPTIONS`); a refused key stops the process at startup
  with exit code 2, as before. `cookiefile`, `proxy`, a PO token and every credential belong
  here.
- A Mirror's `engine_options` pass an allowlist (`FEED_OPTIONS`), each key with a check of its
  value's shape. The allowed params cannot run a command, read or write a path, change where
  traffic goes or carry a credential:
  - format order: `format_sort`, `format_sort_force`, `prefer_free_formats`;
  - pacing and retries, bounded: `ratelimit`, `throttledratelimit`, `sleep_interval`,
    `max_sleep_interval`, `sleep_interval_requests`, `sleep_interval_subtitles`, `retries`,
    `fragment_retries`, `extractor_retries`, `file_access_retries`, `socket_timeout`,
    `concurrent_fragment_downloads` (1 to 16), `skip_unavailable_fragments`;
  - subtitles: `writeautomaticsub`, `subtitlesformat`, and `subtitleslangs`, whose entries
    must be language codes (`en`, `pt-BR`, `-live_chat`, `all`), never a pattern, since
    yt-dlp compiles them as regular expressions;
  - `geo_bypass`, `geo_bypass_country`, `geo_bypass_ip_block`, `age_limit`;
  - `extractor_args` for `youtube` (`lang`, `player_client`, `player_skip`, `skip`,
    `formats`), `youtubetab` (`approximate_date`, `skip`) and `generic` (`impersonate`) only,
    every value a list of strings.

  Everything else is refused, `format` and `writesubtitles` included (they are Copycast's).
  `create_mirror` and `update_mirror` answer `422 engine-option-rejected`, with `keys` naming
  every refused path (`netrc_cmd`, `extractor_args.youtube.po_token`) and `scope: "feed"`.
- `extractor_args` merge per extractor, then per argument. A Mirror's
  `youtube.player_client` keeps the operator's `youtube.po_token` and `youtubepot-*` settings
  from `[engine.options]`.

**Rows written before 2.3.0** are never rewritten by a migration. Every engine call filters
the stored options through the same allowlist (`adapters.engine.options.stored_feed_options`)
and logs `engine_options.refused feed_id=… keys=[…]` at warning level for what it drops.
`MirrorRead` and `feed.json` show the filtered options only, so a `read` key no longer sees a
credential an old row may hold. Because the Settings dialog is filled from that filtered
set, saving it unchanged sends no options and leaves the row alone; any edit of the options
there, or a `PATCH /api/mirrors/{id}` with the allowed set or `{}`, writes the filtered dict.

**MCP scope.** `create_mirror` with non-empty `engine_options`, and `update_mirror` with any
`engine_options` (`{}` included), need a `full` API key. The plan makes per-Mirror options
admin-only once 3.0 has users; the scope is the 2.3 stand-in. Over the HTTP API nothing
changes: the operator pair is enough, or nothing with authentication off. The allowlist is
what protects that path.

## Consequences

- After the upgrade, a feed row that still holds a refused key logs a warning on every
  engine call until its options are rewritten. The UI and the API never show the refused
  keys, so the dialog offers nothing to remove, and a row that holds only refused keys shows
  `{}`, which the dialog does not send again. The operator clears such a row with
  `PATCH /api/mirrors/{id}` and `{"engine_options": {}}` (curl sends no `Origin`, so the
  cross-site guard lets it through) or with
  `UPDATE feeds SET engine_options = '{}' WHERE id = …`.
  `SELECT id, engine_options FROM feeds WHERE engine_options <> '{}'` lists the candidates.
- A manual PO token, cookies, a proxy or `http_headers` per Mirror are gone. The first three
  move to `[engine.options]`. Headers would need a header-name allowlist of their own.
- `retries`, `fragment_retries`, `socket_timeout`, `writeautomaticsub` and `subtitlesformat`
  are allowed but overlaid by Copycast's own options on a fetch, so a Mirror's value only
  affects listings. That was already the case.
- The allowlist was reviewed against yt-dlp 2026.10.29. A param a later release adds stays
  refused until it is listed. Arguments of an allowed extractor are allowlisted by name, so
  only a change in what an existing name means would slip through: re-review on large engine
  bumps.
