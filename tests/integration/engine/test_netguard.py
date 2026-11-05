"""Real yt-dlp over the origin fixture server: the connections it opens on its own are guarded.

``localhost`` stands for a public host (exempt by name); the same server reached as
``127.0.0.1`` stands for an internal one (loopback, not exempt). The Engine is handed
the public URL; whatever yt-dlp is then redirected to is judged when it connects.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from copycast.adapters.engine.ytdlp import YtDlpEngine
from copycast.adapters.sources.guard import install_policy
from copycast.application.ports import CancelToken, FetchSpec, SynthItem
from copycast.domain.destinations import DestinationPolicy
from copycast.domain.enums import FetchKind
from copycast.domain.exceptions import DestinationNotAllowed
from tests.support.origin import Origin

INTERNAL = "/latest/meta-data/"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
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


class _Proxy:
    """A forward HTTP proxy that answers by itself: ``/page`` redirects to ``redirect_to``, a
    path under ``tests/fixtures`` ending in ``.xml`` is that feed, anything else is the secret
    an internal host would hand out."""

    def __init__(self) -> None:
        self.requested: list[str] = []
        self.redirect_to = ""
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                proxy.requested.append(self.path)
                if urlsplit(self.path).path == "/page":
                    self.send_response(302)
                    self.send_header("Location", proxy.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                path = urlsplit(self.path).path
                feed = FIXTURES / path.lstrip("/")
                if path.endswith(".xml") and feed.is_file():
                    body, kind = feed.read_bytes(), "application/rss+xml"
                else:
                    body, kind = b"SECRET", "text/plain"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Proxy]:
    for name in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    running = _Proxy()
    try:
        yield running
    finally:
        running.close()


PROXY_SOURCES = ("option", "environment")


def _through(proxy: _Proxy, how: str, options: dict[str, Any], monkeypatch: Any) -> dict[str, Any]:
    """The proxy (judged as the peer, exempt as ``localhost``) from ``[engine.options]`` or
    from the worker's environment, which yt-dlp reads when ``proxy`` is unset."""
    url = f"http://localhost:{proxy.port}"
    if how == "environment":
        monkeypatch.setenv("HTTP_PROXY", url)
        return dict(options)
    return {**options, "proxy": url}


@pytest.mark.parametrize("how", PROXY_SOURCES)
@pytest.mark.parametrize("options", HANDLERS.values(), ids=HANDLERS.keys())
def test_behind_a_proxy_a_redirect_to_an_internal_host_is_refused(
    proxy: _Proxy, options: dict[str, Any], how: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proxy connects on the Engine's behalf: the destination is judged on its own."""
    proxy.redirect_to = "http://169.254.169.254/latest/meta-data/"
    params = _through(proxy, how, options, monkeypatch)
    with pytest.raises(DestinationNotAllowed, match=r"169\.254\.169\.254:80 is not an allowed"):
        YtDlpEngine().list_source("http://93.184.215.14/page", params, CancelToken(), _Log())
    assert proxy.requested == ["http://93.184.215.14/page"]


@pytest.mark.parametrize("options", HANDLERS.values(), ids=HANDLERS.keys())
def test_behind_a_proxy_a_public_listing_still_works(
    proxy: _Proxy, options: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    params = _through(proxy, "option", options, monkeypatch)
    url = "http://93.184.215.14/rss/itunes_podcast20.xml"
    listing = YtDlpEngine().list_source(url, params, CancelToken(), _Log())
    assert listing.items
    assert proxy.requested == [url]
