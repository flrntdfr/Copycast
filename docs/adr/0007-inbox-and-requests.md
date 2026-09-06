# 0007 Inbox feeds fed by Requests

Status: accepted (v1)

## Context

Not everything worth listening to is a podcast. A single conference talk, a YouTube video,
a SoundCloud set: creating a Mirror per URL would litter the app with one-item feeds that
never change. Agents in particular want "save this for later" with no ceremony, and the
result should show up in the same podcast app as everything else.

## Decision

An Inbox is a Feed without a Source. It is filled by Requests: one URL each, recorded with
`requested_via` (ui or mcp) and expanded asynchronously by the worker into Catalog items
(`Content-Type: audio/*` becomes one synthesized item; pages and playlists are listed by the
engine, capped at 1000 leaves; a podcast RSS URL is refused with "use create_mirror").
Expanded items are wanted immediately and archived like any Episode. A default Inbox named
"Copycast" always exists, is created idempotently by both processes, can be renamed and
cannot be deleted. Inboxes are the only Feeds with Retention: autoprune N days after an
item's first download (off by default; never-downloaded items exempt) and on-demand prune by
criterion (downloaded at least once, added more than X days ago; criteria AND together;
synchronous with a dry run). Inbox deletions also unlist the row so it disappears.

## Consequences

- MCP `add_to_inbox` defaults to the "Copycast" Inbox (id or case-insensitive name).
- The Inbox Feed is fully synthesized (no Source XML to preserve) and ordered by the time
  each item was added.
- Download counting (ADR 0010) is what makes autoprune safe: an item nobody fetched is
  never pruned automatically.
