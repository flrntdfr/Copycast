# 0020 A feed's media key in its enclosure URLs

Status: accepted (2.0.2); amends ADR 0011, narrows ADR 0013

## Context

ADR 0011 left enclosure and asset URLs bare while authentication is on, expecting podcast
apps to reuse the feed's Basic pair for same-host downloads. Overcast does that for a
download (its URLSession answers the 401 challenge and retries), but not for streaming: iOS
AVPlayer sends `Range: bytes=0-` without credentials, gets 401 and never retries. In
production on 2026-10-27 every streaming attempt stopped there and the player stayed on
"loading", while downloads of the same Episode worked. Overcast's server-side fetcher gets
401 on media for the same reason. Credentials as `user:pass@` in each enclosure would leak
the pair into every item and are not reliably honoured by AVPlayer either.

## Decision

While authentication is on, a feed publishes its media, chapters, transcripts and show-note
attachments under `/feeds/{id}/k/{key}/media/…` and `/feeds/{id}/k/{key}/assets/…`, where
`key` is the feed's **media key**: 22 base32 characters of HMAC-SHA256 over the feed id,
keyed by the feed's password (`domain.credentials.feed_media_key`). The keyed routes serve
the same handlers as the bare ones and need nothing else; `require_feed_access` reads the key
from the path only, never from a query, and a wrong key still passes with the feed's or the
operator's pair. Bare URLs keep working with the pair, so the UI and the API's `media.url`
are unchanged. Artwork keeps its bare, public URL (ADR 0011's amendment). With
authentication off nothing changes: no key, no `/k/` in any URL.

Deriving the key instead of storing it means no migration, no new column and nothing new in
`feed.json`: rotating the pair rotates the key, and a rebuild gets it back with the
password. The request log writes the key as `***`.

## Consequences

- Every enclosure URL of an authenticated deployment moves once, on the upgrade to 2.0.2, and
  again when a feed's pair is rotated. ADR 0013 holds otherwise: apps treat the moved URL as
  a new file, so downloaded Episodes may download again on each device once.
- The key is a bearer credential in a URL: anyone holding the Mirror Feed's document can
  stream its media, which is no more than the pair it was fetched with already opens. A
  reverse proxy that logs request paths (Traefik's access log) records keys; Copycast's own
  log does not.
- The shared Feed cache keys now carry the app version, so feeds rendered by a previous
  release (bare enclosures at the same revision) are never served after an upgrade.
- Per-listener Feed keys (the multi-user plan) can replace the derivation later without
  changing the route shape.
