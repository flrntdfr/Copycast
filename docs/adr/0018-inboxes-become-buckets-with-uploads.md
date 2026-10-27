# 0018 Inboxes become Buckets, and files can be uploaded into any Bucket

Status: accepted; renames the Inbox of ADR 0007 and ADR 0015, extends ADR 0015

## Context

"Inbox" suggested something to empty, like mail. People used these Feeds as containers they
fill on purpose: URLs pushed by them or their agents, YouTube playlists captured from the
same section, and now files from their own disk. The Settings page already called every
Feed's storage a bucket, so the product had two words for one idea.

Uploads (ADR 0015) only fed a new Serial Inbox, built by an agent that decides the order. A
person with a folder of files had no way in from the UI, and could not add to an existing
feed at all.

## Decision

**Inbox is renamed Bucket everywhere**: UI copy, routes (`/buckets`, `/api/buckets`,
`/api/serial-buckets`), MCP tools (`create_bucket`, `update_bucket`, `add_to_bucket`,
`prune_bucket`, `create_serial_bucket`), API fields and events, code and docs. There are no
aliases for the old API and tool names: agents read the tool list on connect. Persisted data
moves with a migration (0014: `feeds.kind = 'inbox'` becomes `'bucket'`, the check
constraints follow) and `FeedKind` reads `inbox` as `bucket`, so a `feed.json` written before
still rebuilds. Feed ids, titles and feed URLs do not change, so podcast apps notice nothing.
The web keeps `/inboxes` and `/inboxes/{id}` as redirects for old bookmarks. Earlier ADRs
keep the word they were written with.

**`add_uploads` adds received uploads to an existing Bucket** (route
`POST /api/buckets/{bucket_id}/uploads` and MCP tool). Copycast orders the batch by natural
file-name order (digit runs compare as numbers, case is ignored) and dates each Episode by
arrival, one second apart with the last one now, so the order holds in every podcast app
and the batch lands after what the Bucket already holds. Titles come from the files' tags,
else their names. A Serial Bucket takes them too; they land after the work, like Requests.

**The Bucket page uploads files**: *Upload files…* sorts the picked files the same way,
stages and `PUT`s them one at a time with a progress bar each, and calls `add_uploads` with
those that arrived.

## Consequences

- MCP clients and scripts that called the `*_inbox` tools or `/api/inboxes` must switch.
- The ordering is fixed by the server, so an agent that wants another order uses
  `create_serial_bucket`.
- A failed file in a batch does not stop the others; it stays staged until swept after 24 h.
