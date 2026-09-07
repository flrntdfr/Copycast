# 0009 Copycast never deletes from a Mirror; user deletions leave Tombstones

Status: accepted (v1); narrowed by [ADR 0012](0012-mirror-modes-rolling-and-automatic.md): a Mirror in Rolling or Automatic mode deletes on its own, by explicit choice

## Context

A Mirror exists because the Source may vanish. Any automatic deletion — retention, "the
Source removed it", disk pressure — would defeat that. But people do delete: a mistaken
bulk archive, an episode they will never listen to. A deleted item must not come back on the
next Refresh, and must still be archivable on purpose later.

## Decision

Mirrors have no Retention and no automatic deletion of any kind; items the Source drops
become Delisted and stay in the Mirror Feed with their media. The only deletions are the
ones a person requests: deleting an Episode unlinks its media, sidecar and item assets,
keeps the original `<item>` XML, and leaves a Tombstone — the Catalog row marked `deleted`,
shown as Available. Policy (Backfill, Follow) never re-archives a tombstoned item; only an
explicit archive request (UI, `archive_item`, a selection) does, and then the item is
simply archived again. Deleting a Mirror removes everything, cancels its jobs first, and is
the one operation MCP guards with `confirm=true`. Inboxes follow the same rules except that
Retention (ADR 0007) is their deliberate exception and their deletions also unlist the row.

## Consequences

- "Delete" in the UI means "I will decide again later", never "forget it existed".
- The published feed changes only when a person deletes; Source churn never shrinks it.
- Storage grows monotonically for Mirrors; the About page and per-feed sizes make that
  visible instead of hiding it behind a retention knob.
