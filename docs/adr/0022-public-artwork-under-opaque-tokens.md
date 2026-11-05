# 0022 Public artwork under opaque tokens; one answer for every refusal under /feeds; feeds ask directories not to list them

Status: accepted (2.3.0); amends ADR 0011 (its artwork amendment) and ADR 0020 (Artwork's bare
URL)

## Context

The 2.3.0 security pass (findings U1 to U3 of the configuration and users plan) found three
ways a stranger could learn what a Copycast server mirrors without holding a single
credential:

- **Artwork at a computable URL.** ADR 0011's amendment made
  `/feeds/{id}/assets/*.artwork.*` public because podcast apps fetch images without the feed's
  pair. It argued that the URLs "are not enumerable", but they are computable: a Mirror ID is
  `sha256` of the Source's dedup key, an item id `sha256` of the feed id and the Source key.
  A request for `/feeds/{sha256(channel URL)[:16]}/assets/feed.artwork.jpg` confirmed that the
  server mirrors channel X, and the same for an archived video Y. A Watch Later Capture has
  the same id on every install.
- **Existence oracles.** The feed routes answered "wrong media key for this feed", "wrong
  username or password for this feed" or 404 depending on what existed, so a wrong guess
  still told the caller whether the feed, the item or the asset was there.
- **Directories.** Nothing in a Mirror Feed told Apple Podcasts or Podcast Index not to list
  it, so a feed URL pasted into a directory once could make a personal archive public.

## Decision

**Artwork has one public URL: `/art/{asset_id}-{mac}.{ext}`.**
- `mac` is the first 26 characters (130 bits) of the lowercase, unpadded base32 of
  `HMAC-SHA256(instance_secret, b"copycast artwork\x00" + asset_id)`
  (`domain.credentials.artwork_mac`); `ext` is the stored file's extension.
- The instance secret is 32 random bytes in the Postgres table `instance_secrets`, row
  `artwork` (migration 0016), minted on the api's first start with
  `INSERT … ON CONFLICT DO NOTHING` and read back, so processes starting together agree on
  one. Each process reads it once and keeps it. Nothing new goes into the configuration.
- The route takes no credential in either auth mode. It answers `GET` and `HEAD` with Range
  and ETag (`ObjectResponse`, ADR 0019) and `Cache-Control: public, max-age=86400`.
- The MAC is compared in constant time before anything is looked up. Every failure is the
  same 404 ("no such artwork"): a malformed token, a wrong MAC, an unknown asset, an asset
  that is not archived Artwork, a wrong extension or a missing object.
- Every rendered feed, and `AssetRead.url` of an Artwork asset in the API and MCP, uses this
  URL, whatever the auth mode. The Copycast logo (`/feeds/copycast-artwork.png`) stays public
  at its own path.
- `/feeds/{id}/assets/*.artwork.*` loses its exemption. While authentication is on it takes
  the feed's pair, the operator pair or the media key, like every other asset; with
  authentication off it answers as before (ADR 0004).

**One answer for every refusal under `/feeds`.** `require_feed_access` is a router dependency,
so it runs before the endpoint looks anything up. While authentication is on, a missing pair,
a wrong pair, another feed's pair, a wrong media key and an unknown feed, item or asset all
get the same 401, with the `Basic` challenge and the detail "this feed requires its username
and password". An unknown feed is compared against a stand-in pair so that it costs the same
work as a known one. The plan asked for a 404 on keyed paths. Keyed paths answer the same
401 as the rest instead, because ADR 0020 lets a valid feed pair open a keyed URL even with a
wrong or rotated key, and a client answering the challenge must still get through. Each route
family gives one answer, so it is no oracle either way.

**Feeds ask directories not to list them.** Every feed's channel carries
`<itunes:block>Yes</itunes:block>` and `<podcast:block>yes</podcast:block>`, and a Source's
own `block` elements (in any of the namespace spellings seen in the wild) are dropped, never
duplicated. This applies to every feed, not only authenticated ones: a personal archive is
nothing a directory should list.

**Feeds move once, visibly.**
- `Last-Modified` and `lastBuildDate` never go below `render.RENDER_FORMAT_SINCE`
  (2026-11-05T00:00Z). A client revalidating with `If-Modified-Since` alone would otherwise
  keep a pre-2.3.0 body through 304s for as long as the feed itself did not change.
- The shared Feed cache's keys carry 16 hex characters of a digest of the instance secret
  (`copycast:feed:{version}:{namespace}:{feed}:{revision}`). A database rebuilt from scratch
  mints a new secret, and must not be served the old installation's bodies at a revision
  number they happen to share.

## Consequences

- On the upgrade to 2.3.0 every Artwork URL moves once to `/art/`. Only images move:
  podcast apps refetch them and download no audio again, since enclosure URLs are unchanged
  (ADR 0013, ADR 0020). Every feed's `Last-Modified` moves forward once, to the floor. A feed
  whose last change fell between the floor and the actual deploy is not covered by it;
  `If-None-Match` clients are unaffected.
- The secret lives in Postgres, not in the Object store. `copycast rebuild` onto an empty
  database mints a new one, so every `/art/` URL changes once more. A running api keeps the
  secret it read, and a replica started later would read the new one: restart the api after
  such a rebuild. A `pg_dump` now holds the secret; losing it changes image URLs and
  nothing else.
- Scripts or monitors that matched the old 401 details ("wrong media key", "wrong username
  or password for this feed") need the new text. The operator's 401 on `/api` still says
  "wrong username or password".
- `FeedRead.artwork_url` and `ItemRead.artwork_url` still carry the Source's remote image
  URL. Only the archived file's `AssetRead.url` moved. The SPA's header and the player's
  Media Session load those remote fields from the Source.
- `/art/` with a valid MAC still queries Postgres and the Object store, and the pair and
  media-key comparisons do not equalize the database time of a hit and a miss. The answers
  are uniform; their timing is only roughly so.
- Feed ids stay hashes of the Source URL, so a feed URL is still computable; it opens nothing
  without the pair while authentication is on. The 3.0 plan proposes salting them.
