# 0010 What counts as a download

Status: accepted (v1)

## Context

Inbox autoprune (ADR 0007) deletes items N days after their first download and must never
delete something nobody listened to. Podcast clients fetch media in many ways: a full GET,
a HEAD to read the size, byte-range requests while streaming, a prefetch of the first
kilobytes, resumed downloads. Counting every request would prune too eagerly; counting only
complete transfers is impossible to know server-side.

## Decision

A media fetch counts as one download when it is a `GET` with no `Range` header or with a
range starting at byte 0 (`bytes=0-...`). `HEAD` never counts; a range starting later never
counts; assets (Artwork, chapters, transcripts) never count. The count and the first and
last download timestamps are recorded in a background task after the response starts,
never on the request path. Download counts are telemetry: they are not exported to the
descriptor and are lost by a rebuild.

## Consequences

- A stream that starts at byte 0 counts once, however many later ranges it uses.
- A prefetch of the first bytes counts as a download; erring in that direction is safe
  because autoprune waits N days after it and prunes nothing never fetched.
- The rule is a unit-tested table; the media route is the only caller of `record_download`.
