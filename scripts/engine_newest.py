#!/usr/bin/env python3
"""Print the newest yt-dlp version published on PyPI for a release channel.

    uv run python scripts/engine_newest.py --channel nightly
    uv run python scripts/engine_newest.py --channel stable

``nightly`` considers pre-releases (yt-dlp publishes its nightly builds to PyPI as
pre-release versions); ``stable`` considers final releases only. Releases without files or
with every file yanked are ignored. The version is printed in its PEP 440 normalized form,
the same form uv writes into uv.lock, so ``engine-bump.yml`` can compare it with the output of
``scripts/engine_version.py`` verbatim.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Literal, cast

import httpx
from packaging.version import InvalidVersion, Version

ENGINE_PACKAGE = "yt-dlp"
PYPI_JSON_URL = f"https://pypi.org/pypi/{ENGINE_PACKAGE}/json"
TIMEOUT_SECONDS = 30.0

Channel = Literal["nightly", "stable"]


class NoRelease(Exception):
    """PyPI lists no usable release for the channel."""


def _usable(files: object) -> bool:
    """A release is usable when at least one of its files is not yanked."""
    if not isinstance(files, list) or not files:
        return False
    for raw in cast(list[object], files):
        if isinstance(raw, dict) and not cast(dict[str, object], raw).get("yanked", False):
            return True
    return False


def newest_version(releases: dict[str, Any], channel: Channel) -> Version:
    """The newest usable version among PyPI ``releases`` for ``channel``."""
    candidates: list[Version] = []
    for raw, files in releases.items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if version.is_prerelease and channel != "nightly":
            continue
        if version.is_devrelease and channel != "nightly":
            continue
        if not _usable(files):
            continue
        candidates.append(version)
    if not candidates:
        raise NoRelease(f"no usable {channel} release of {ENGINE_PACKAGE} on PyPI")
    return max(candidates)


def fetch_releases(client: httpx.Client | None = None) -> dict[str, Any]:
    """The ``releases`` map of the package's PyPI JSON document."""
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
    try:
        response = client.get(PYPI_JSON_URL, headers={"Accept": "application/json"})
        response.raise_for_status()
        document: object = response.json()
    finally:
        if own:
            client.close()
    releases: object = (
        cast(dict[str, object], document).get("releases") if isinstance(document, dict) else None
    )
    if not isinstance(releases, dict):
        raise NoRelease("PyPI document has no 'releases' map")
    return cast(dict[str, Any], releases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--channel",
        choices=("nightly", "stable"),
        default="nightly",
        help="release channel (default: nightly)",
    )
    args = parser.parse_args(argv)
    channel: Channel = args.channel
    try:
        print(newest_version(fetch_releases(), channel))
    except (httpx.HTTPError, NoRelease, ValueError) as exc:
        print(f"engine_newest: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
