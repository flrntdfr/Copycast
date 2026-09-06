# 0008 Ordinals, Source numbering and selections

Status: accepted (v1)

## Context

People and agents refer to episodes by number: "archive 1 to 42 and 180". Sources disagree
about what a number is: RSS may carry `itunes:episode`, playlists have an index, channel
tabs have nothing, and all of them list newest first while people count from the oldest.
Numbers must also survive the Source dropping or reordering items, otherwise a selection
made today means something else tomorrow.

## Decision

Every Catalog item gets an Ordinal when first seen: items are numbered from the oldest
(`published_at` when every new item has one, else by listing position, reversed for
newest-first listings), continuing from the feed's current maximum. Ordinals are assigned
once and never renumbered, so gaps are normal. Sources declare their listing order: RSS and
channel tabs are newest first; playlist extractors are oldest first with
`source_number = playlist_index`.

Source numbering wins for display and for agent requests when it exists and is unique; the
Ordinal is the fallback. A selection expression (`1-42, 180`) is resolved to item ids at
request time against the chosen numbering, reports unresolved numbers and the count already
archived, and can be dry-run. Creating a Mirror with a selection lists the Source
synchronously, archives exactly the selection and turns Follow off by default.

## Consequences

- A selection is exact: the next Refresh archives nothing new unless Follow is on.
- Podcast clients see `itunes:episode` = Source number, else Ordinal.
- A rebuild reproduces Ordinals from the descriptor, never from the current listing.
