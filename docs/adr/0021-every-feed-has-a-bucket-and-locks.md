# 0021 Every Feed has a bucket; Local feeds; the Lock alone protects an Episode

Status: accepted; renames ADR 0018's Bucket in the UI and documentation, and amends ADR 0016
(what protects an Episode when its bucket is emptied)

## Context

After 2.1.0 the operator described Copycast in one sentence: everything is a feed, and every
feed has a bucket underneath it. The vocabulary said otherwise. "Bucket" named the Feed with no
Source, while the files every Feed keeps in the Object store had no name of their own, and the
Feeds page showed captured playlists among the Buckets rather than with the feeds.

Protection was split in two. The Archive flag kept one Episode, and a Mirror on the Archive
policy kept all of them through a blanket rule in `clear_feed`. Neither showed which files would
survive emptying a bucket, and a flagged Episode could still be deleted from its row.

## Decision

- **Feed** stays the umbrella. A Feed is a **Mirror** (a captured YouTube playlist included) or
  a **Local feed**, the Feed with no Source that Requests and Uploads fill. The Feeds page lists
  all of them in one table; the UI no longer creates Local feeds by hand.
- A **bucket** is a Feed's files: everything under `feeds/{id}/`. Every Feed has exactly one.
  Below the table, a collapsed *Buckets* box shows one card per Feed with its file count and
  size (`storage_summary`, one listing of `feeds/`). A bucket's page lists every file with its
  kind, size, a padlock and a download link (`list_feed_objects`), and offers *Purge*.
- The **Lock** is the Archive flag renamed in the UI and documentation (`archive_flag` and
  `set_archive_flag` keep their names). A Locked Episode is never deleted by Retention, a
  Rolling window, Sync, a Purge (`clear_feed`), the admin purge or `delete_item`, which answers
  409. Only deleting the whole Feed removes it.
- The **Archive policy Locks what it archives**: an Episode archived while its Mirror is on an
  Archive mode is Locked, and switching a Mirror to Archive Locks the Episodes it holds.
  Switching away unlocks nothing. A Mirror with Sync (every captured playlist) is never
  Locked automatically: Sync skips Locked Episodes, so an automatic Lock would silently switch
  Sync off. Turning Sync off on an Archive Mirror Locks what it holds. Migration 0015 Locks the
  archived Episodes of Mirrors already on Archive without Sync and bumps their intent so
  `feed.json` carries the Locks. `clear_feed` drops its blanket rule: only the Lock protects.
- The API and MCP keep `kind: "bucket"`, `/api/buckets`, `BucketRead`, `add_to_bucket`,
  `create_bucket`, `create_serial_bucket` and `prune_bucket`, so no client breaks. Only the UI
  and the documentation say Local feed.

## Consequences

- The bucket page has no Settings: a Local feed's name and autoprune live in its feed page's
  Settings dialog, next to *Add URL* and *Upload files…*. The criteria-based *Prune now…*
  dialog leaves the UI; `prune_bucket` stays for agents.
- The Settings page loses its storage table, and Export OPML moves there from About.
- Unlocking an Episode of an Archive Mirror by hand makes it purgeable, which is the point:
  the operator decides per Episode, and the policy only sets the starting state.
