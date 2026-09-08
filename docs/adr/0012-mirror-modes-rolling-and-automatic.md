# 0012 Mirror modes: Rolling windows and Automatic archives narrow ADR 0009

Status: accepted

## Context

ADR 0009 made a Mirror an archive: nothing is deleted unless a person asks. Two uses do not
fit that shape. A daily news show is worth the last ten episodes and nothing older; a
channel with a thousand videos is worth having in a podcast app without a thousand
downloads up front. Both want Copycast to delete on its own, which ADR 0009 forbids.

## Decision

Two modes are added to the Backfill policy, each an explicit, per-Mirror opt-in that the
operator confirms when it would delete something:

- **Rolling**: the newest N items by publication are archived and stay archived; as newer
  ones arrive, archived items outside the window are tombstoned and their media removed.
  The window is applied at every Refresh and immediately when a Mirror is switched to it.
- **Automatic**: nothing is archived ahead of time. The Mirror Feed lists every item the
  Source lists; an Episode is archived the first time a podcast app asks for its media (the
  request waits up to two minutes, then answers 503 with `Retry-After` while the download
  continues). Archived Episodes expire (are tombstoned) after `retention_days` without a
  download, 7 by default; `null` keeps them forever.

Everything, Selection and the legacy Latest N keep ADR 0009 whole. Rolled-out and expired
Episodes leave the same Tombstone as a user deletion: never re-archived by policy, listed as
Available, archivable on purpose. A `MirrorUpdate` can be previewed
(`preview_mirror_update`, a route without an MCP tool) so the UI asks for confirmation
naming what the change deletes; over MCP, the modes that delete need a `full` key, like an
Inbox's Retention.

## Consequences

- "Copycast never deletes from a Mirror" now reads "unless the Mirror's mode says so, and
  you were told when choosing it".
- Latest N is no longer offered for new Mirrors; Rolling N covers the intent people had.
- An Automatic Mirror's feed advertises a placeholder `.mp3` enclosure for items not
  archived yet, with a length estimated at 128 kbit/s from the duration (podcast apps refuse
  0-byte enclosures); the media route accepts that extension and serves whatever container
  the archive produced.
