# 0002 The engine runs in process, pinned by the lockfile, nightly channel by default

Status: accepted (v1); supersedes the v0 decision to download a yt-dlp binary at build time

## Context

v0 drove a downloaded yt-dlp binary as a subprocess, with its version pinned in the
application config. Progress, cancellation, error classification and log capture all went
through stdout parsing. Site breakage is frequent and the fix is almost always "a newer
yt-dlp"; the nightly channel gets fixes days before stable.

## Decision

yt-dlp is a Python dependency (`yt-dlp[default]`) imported as a library by exactly one
module, `copycast.adapters.engine` (an import-linter contract enforces this). The worker
runs one `YoutubeDL` per call in a thread, with progress hooks, a cancel token and a logger
bridge. Every download goes through the engine, including RSS enclosures, for which the
adapter synthesizes an info dict and calls `process_ie_result`. Audio is stream-copied, best
audio preferring m4a, with tags, chapters and Artwork embedded; Artwork the file already
carries is kept. mp3 and m4a are never re-encoded; a codec podcast apps do not play (Opus,
Vorbis, FLAC, WAV) is transcoded once to mp3 at 192 kbit/s so every archive is predictable.

The image carries ffmpeg and `deno` (the JavaScript runtime yt-dlp uses for YouTube's
challenges), both pinned. The version is pinned in `uv.lock`, installed as its own image layer, bumped by automation
(`engine-bump.yml`, every six hours, nightly channel by default, `vars.ENGINE_CHANNEL` to
switch) and recorded on every archived item. The engine version and release date are shown
on the About page.

## Consequences

- Upgrading the engine means a new image, not a config change; ADR 0006 makes that cheap
  and reversible.
- Engine failures are classified into transient, permanent, storage-full and cancelled in
  one place (`errors.classify`); the worker retries only transient ones.
- Options users may pass (`[engine.options]`, per-Mirror `engine_options`) are filtered
  against the options Copycast owns, and `cookiefile`/`proxy` are global only.
- Tests never load the real engine except in `tests/integration/engine`; everything else
  uses `FakeEngine` through the `Engine` port.
