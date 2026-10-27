# 0015 Serial Inboxes: an agent uploads files and gives their listening order

Status: accepted (v1.4)

## Context

People want an audiobook (or any work split into files: a lecture series, a radio drama)
in Overcast, played in order. The files are local, not on a URL a Request could fetch; their
order is spread over ID3/MP4 tags (disc, track, title), file names and embedded chapters,
and often contradicts itself (track numbers restarting per disc, "Chapter 10" sorting before
"Chapter 2"). An Inbox orders Episodes by the time they were added and dates them that way,
and a podcast app plays oldest first only when the feed says `itunes:type serial` and the
dates agree.

## Decision

**Files come in by upload over HTTP.** `create_upload` (route and MCP tool) stages one file
and returns a one-time `upload_url`; a `PUT` of the bytes there (`put_upload`, route only:
a tool argument cannot carry an audiobook) receives it, reads its tags with mutagen
(title, album, artist, track and disc with totals, duration, ID3 `CHAP` or MP4 chapters)
and returns them; `get_upload` reads them again. The URL lives outside `/api` and its
random token is its only credential, so an agent holding just an API key can
`curl -T file <upload_url>`; the token is stored as a SHA-256 digest. Staged uploads are
files under `data/uploads/<id>/`, not rows: they are transient, swept after 24 h, and
removed once a Serial Inbox takes them. Only mp3, m4a and MPEG-4 audio (m4b, mp4, served
as m4a, bytes unchanged) are accepted; anything that would need transcoding is refused.
The files do not go through the Engine: nothing is downloaded.

**The agent decides the order.** Copycast reports metadata; it does not guess. The agent
reads the tags and file names, decides the sequence and calls `create_serial_inbox` with
`episodes` first to last, each with its `title`, optional `author`, `part`/`part_title`,
`chapter` and `description`. A wrong guess by the server would be silent; a wrong guess
by the agent is visible in its own reasoning and correctable by the person.

**A Serial Inbox stores and renders the order.** `feeds.serial` (migration 0012, in
`feed.json` and the rebuild) marks an Inbox created this way. Its Episodes are archived at
creation, in one transaction: Ordinal and `itunes:episode` are the position (1-based),
`itunes:season` the part, the title is prefixed with the zero-padded position unless
`number_titles` is false, and each description starts with the Episode's place in the work
("Part 2: The Return · Chapter 5 · Episode 7 of 42 · starts 2:13:05 into 18:04:00 (12%)").
The feed says `itunes:type serial` and dates each Episode by its `published_at`, not its
arrival: one per day, ending yesterday, each at a posting time picked for fun (01:23,
03:14, 13:37, 23:59...). Because every Episode has its own day, the joke never decides the
order, and no date is in the future, which some apps hide.

A Serial Inbox is otherwise an Inbox: renamable, deletable, prunable, Retention off.
Requests into it are possible but date by arrival and land after the work.

## Consequences

- Agents get the same contract as everything else (ADR 0003): three tools and four
  routes, the `PUT` exempt from MCP.
- Embedded chapters are reported to the agent but not yet published as Podcasting 2.0
  chapters; the archive job's chapter work (in progress) can reuse them later.
- An upload directory abandoned mid-batch costs disk until the next `create_upload` sweeps
  it.
- The posting dates are synthetic by design: they encode order, not history.
