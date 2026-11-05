"""Real yt-dlp over the origin fixture server: the connections it opens on its own are guarded.

``localhost`` stands for a public host (exempt by name); the same server reached as
``127.0.0.1`` stands for an internal one (loopback, not exempt). The Engine is handed
the public URL; whatever yt-dlp is then redirected to is judged when it connects.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from copycast.adapters.engine.ytdlp import YtDlpEngine
from copycast.adapters.sources.guard import install_policy
from copycast.application.ports import CancelToken, FetchSpec, SynthItem
from copycast.domain.destinations import DestinationPolicy
from copycast.domain.enums import FetchKind
from copycast.domain.exceptions import DestinationNotAllowed
from tests.support.origin import Origin

INTERNAL = "/latest/meta-data/"
HANDLERS = {
    "requests": {},
    "urllib": {"compat_opts": ["prefer-legacy-http-handler"]},
}


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def debug(self, message: str) -> None:
        self.lines.append(message)

    def info(self, message: str) -> None:
        self.lines.append(message)

    def warning(self, message: str) -> None:
        self.lines.append(message)

    def error(self, message: str) -> None:
        self.lines.append(message)


@pytest.fixture(autouse=True)
def localhost_is_public() -> Iterator[None]:
    previous = install_policy(DestinationPolicy.from_entries(["localhost"]))
    try:
        yield
    finally:
        install_policy(previous)


def public(origin: Origin, path: str) -> str:
    return f"http://localhost:{origin.port}{path}"


@pytest.mark.parametrize("options", HANDLERS.values(), ids=HANDLERS.keys())
def test_a_listing_redirected_to_an_internal_host_is_refused(
    origin: Origin, options: dict[str, Any]
) -> None:
    """The probe's Engine fallback: a public page that sends yt-dlp to the metadata service."""
    origin.script("/page", status=302, headers={"Location": origin.url_for(INTERNAL)})
    with pytest.raises(DestinationNotAllowed, match=r"127\.0\.0\.1:\d+ is not an allowed"):
        YtDlpEngine().list_source(public(origin, "/page"), options, CancelToken(), _Log())
    assert origin.hits("/page") == 1
    assert origin.hits(INTERNAL) == 0


@pytest.mark.parametrize("options", HANDLERS.values(), ids=HANDLERS.keys())
def test_a_public_listing_still_works_through_the_guard(
    origin: Origin, options: dict[str, Any]
) -> None:
    listing = YtDlpEngine().list_source(
        public(origin, "/rss/itunes_podcast20.xml"), options, CancelToken(), _Log()
    )
    assert listing.items
    assert origin.requests
    assert all(r.headers.get("host") == f"localhost:{origin.port}" for r in origin.requests)


@pytest.mark.parametrize("options", HANDLERS.values(), ids=HANDLERS.keys())
def test_an_enclosure_redirected_to_an_internal_host_is_refused(
    origin: Origin, options: dict[str, Any], tmp_path: Path
) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    origin.script(
        "/episode.mp3", status=302, headers={"Location": origin.url_for(INTERNAL)}, times=100
    )
    url = public(origin, "/episode.mp3")
    synth = SynthItem(id="x", title="A", url=url, mime="audio/mpeg")
    spec = FetchSpec(FetchKind.direct, url, "x", tmp_path / "m", tmp_path / "t", synth)
    with pytest.raises(DestinationNotAllowed):
        YtDlpEngine().fetch_item(spec, options, CancelToken(), lambda _: None, _Log())
    assert origin.hits("/episode.mp3") >= 1
    assert origin.hits(INTERNAL) == 0
