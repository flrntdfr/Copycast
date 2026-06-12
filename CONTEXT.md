# Copycast

A self-hosted podcast mirroring and archiving service. You point it at a source of audio content (podcast feed, YouTube channel, playlist); it keeps a durable local copy and publishes its own feed that keeps working even if the original disappears.

## Language

**Mirror**:
A standing subscription to one Source. Created once, then kept up to date automatically; it continues serving its archived content even when the Source becomes unreachable.
_Avoid_: Archive, snapshot, copy, subscription

**Source**:
The external URL a Mirror tracks — a podcast RSS feed, a YouTube channel or playlist, or anything else the downloader backend supports.
_Avoid_: Original, upstream, target

**Mirror Feed**:
The RSS feed Copycast publishes for a Mirror — the thing you subscribe to in a podcast client. It preserves the Source's original metadata but points at archived audio.
_Avoid_: Output feed, generated feed, proxy feed

**Episode**:
A single audio item archived within a Mirror, together with its preserved metadata. Episode metadata is captured at archive time and is permanent; the Mirror Feed lists the union of every Episode ever archived.
_Avoid_: Item, entry, track, video

**Artwork**:
The cover image of a Mirror and the images of its Episodes, archived alongside the audio and served from the Mirror like any other asset.
_Avoid_: Thumbnail, image

**Delisted**:
The state of an Episode that the Source no longer lists. A Delisted Episode remains in the Mirror Feed unchanged; its state is visible only in the Copycast UI.
_Avoid_: Removed, deleted, orphaned

**Mirror ID**:
A Mirror's permanent, opaque identity, derived from its Source URL at creation. It names the Mirror Feed URL and the Mirror's data directory, and never changes — podcast clients depend on it.
_Avoid_: Slug, name, feed name

**Refresh**:
The recurring act of checking a Source for new Episodes and archiving them. Triggered by schedule, by a manual action, or by a fetch of the Mirror Feed (asynchronously — serving the feed is never delayed, and a cooldown prevents repeated triggering). A failed Refresh never degrades the Mirror Feed; the Mirror keeps serving what it has.
_Avoid_: Sync, update, poll

**Paused**:
The state of a Mirror that no longer Refreshes but keeps serving its Mirror Feed and archived Episodes. The only way to stop a Mirror without destroying its archive.
_Avoid_: Disabled, stopped, archived
