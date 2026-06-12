# yt-dlp version pinned in config, bundled into the image at build time

The exact yt-dlp version is pinned once, in `application.yaml` — never in the
Dockerfile. The Docker build greps the pin out of that file and bakes the
matching release binary into the image at `/opt/copycast/bin`, so a fresh
container needs no network access to become operational. At runtime the app
resolves a binary matching the configured pin in order: the bundled one, a
previously cached one in `<data-dir>/bin`, and only as a fallback a download
from GitHub — which covers bumping the pin in `/config` without rebuilding
the image. The active version and its release date are reported on the main
page. The considered alternatives were declaring the version as a Dockerfile
`ARG` (duplicates runtime configuration; the app could no longer verify or
fetch the pinned version itself) and downloading at first launch (single
source of truth, but startup then depends on GitHub being reachable). We
accepted a small build-time coupling — the Dockerfile parses the config file
— to keep one source of truth, offline-capable startup, and rebuild-free
version bumps. When a site change breaks the pinned extractor, the operator
bumps the pin and either rebuilds or simply restarts.
