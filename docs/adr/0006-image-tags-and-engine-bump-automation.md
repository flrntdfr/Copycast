# 0006 Image tags and automated engine bumps

Status: accepted (v1); supersedes the v0 "yt-dlp version in the config" decision

## Context

ADR 0002 pins the engine in the lockfile, so a fresh engine means a fresh image. yt-dlp
publishes several nightly builds a week. Operators need the newest engine without
following the project, and a way back when a build breaks their site.

## Decision

`engine-bump.yml` runs every six hours: it asks PyPI for the newest version of the
configured channel (`scripts/engine_newest.py`; pre-releases only for `nightly`), pins it
with `uv add "yt-dlp[default]==<version>"` (rewriting the exact pin in pyproject.toml and uv.lock), runs the engine unit and ffmpeg integration tests (the
network flat-extraction test is advisory), and pushes `chore(engine): yt-dlp A -> B` to
`main`. With a GitHub App configured (`vars.ENGINE_BUMP_APP_ID`,
`secrets.ENGINE_BUMP_APP_PRIVATE_KEY`) the push is made with its token, which triggers the
image workflow like any push; without one the push is made with `GITHUB_TOKEN`, which cannot
trigger other workflows, so the image build is dispatched explicitly afterwards.

`image.yml` builds amd64 and arm64 natively, pushes by digest, merges the manifest as
`sha-<short>`, smoke-tests it (`scripts/smoke.sh`: readiness of both processes, the engine
version in `/api/about` equals the lock, ffmpeg present, the default Inbox exists,
`copycast --version`, and a root-owned data directory is refused with a chown hint) and only
then moves the tags: `<app>` (moves on engine bumps), `<app>-yt<engine>` (immutable) and
`latest`. The first build of an app version also creates the git tag `v<app>` and a GitHub
Release. The Dockerfile installs the engine in its own layer so a bump rebuilds one layer.

Dependabot handles every other dependency weekly and ignores yt-dlp.

## Consequences

- "Which engine am I running?" is answered by the image tag, the About page,
  `copycast --version` and `jobs.engine_version`.
- Rollback is pinning the previous `<app>-yt<engine>` tag.
- A broken nightly can reach `latest` only after the smoke test passes; site-specific
  breakage is caught by the advisory network test and by users, which is why the immutable
  tags exist.
