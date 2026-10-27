# 0016 Two policies, Ephemeral and Archive, and the Archive flag

Status: accepted; amends ADR 0012 (the modes offered) and ADR 0009 (what clearing a Feed may delete)

## Context

ADR 0012 offered four Backfill tabs: Everything, Rolling N, Automatic and Selection. In
practice a Mirror is either something you keep or something you listen through. Rolling N
was a halfway house that confused more than it helped. A Selection is what an agent asks for
("1-42, 180"), and a person does the same from the Catalog. Nothing said "keep every new
episode, but not the ten-year backlog".

Storage also lacked a lever. Deleting a Feed was all or nothing, and an Ephemeral Mirror or
an Inbox could not keep one Episode out of its Retention.

## Decision

- The UI offers two policies as tabs. **Ephemeral** is the mode `automatic`, unchanged:
  downloaded when a podcast app asks, expiring after its Retention. **Archive** keeps whatever
  it archives. Its **Backfill** switch picks `all` (everything already published, then every
  new item) or the new mode `new` (only items first seen after the policy is applied).
  Switching to `new` restarts that moment; saving it again does not.
- `rolling`, `selection` and `latest` stay in the API and MCP. A Mirror on one keeps a third
  tab for it until switched.
- An Episode can be flagged **Archive** (`catalog_items.archive_flag`, `set_archive_flag`).
  Retention (Ephemeral and Inbox), a Rolling window and Sync skip a flagged Episode. A user
  deleting it, deleting its Feed, or the admin purge do not.
- **Clearing a bucket** (`clear_feed`, a dry run by default) deletes every archived Episode
  not kept for good and keeps the Feed. Kept for good means flagged, or any Episode of a
  Mirror on the Archive policy. The Settings page's trash button warns when a bucket holds
  kept Episodes and offers *Delete the rest* next to *Delete everything*. The default Inbox
  can only be cleared.
- The API value `automatic` keeps its name; only the UI, documentation and tool descriptions
  say Ephemeral.

## Consequences

- Migration 0013 widens the `backfill_mode` check to `new` and adds `archive_flag` (false for
  existing rows). The flag is written to `feed.json`, so a rebuild keeps it.
- `clear_feed` is destructive, so its MCP tool needs a `full` key. `set_archive_flag` only
  protects, so a `write` key is enough.
- The Settings page saves as you change it. Its Mirrors half now holds the Refresh interval,
  language, minimum length and default policy.
