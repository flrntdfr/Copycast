from __future__ import annotations

import httpx

from tests.support.origin import FIXTURES_DIR, Origin


def test_serves_fixtures_with_etag_and_last_modified(origin: Origin) -> None:
    response = httpx.get(origin.url_for("/rss/itunes_podcast20.xml"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/rss+xml"
    assert response.headers["etag"].startswith('"')
    assert "last-modified" in response.headers
    assert response.headers["accept-ranges"] == "bytes"
    assert response.content == (FIXTURES_DIR / "rss" / "itunes_podcast20.xml").read_bytes()
    assert origin.hits("/rss/itunes_podcast20.xml") == 1


def test_conditional_requests_answer_304(origin: Origin) -> None:
    first = httpx.get(origin.url_for("/media/tiny.mp3"))
    etag = first.headers["etag"]
    again = httpx.get(origin.url_for("/media/tiny.mp3"), headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    since = httpx.get(
        origin.url_for("/media/tiny.mp3"),
        headers={"If-Modified-Since": first.headers["last-modified"]},
    )
    assert since.status_code == 304
    assert origin.requests_for("/media/tiny.mp3")[1].if_none_match == etag


def test_range_requests(origin: Origin) -> None:
    data = (FIXTURES_DIR / "media" / "tiny.mp3").read_bytes()
    partial = httpx.get(origin.url_for("/media/tiny.mp3"), headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206
    assert partial.content == data[:100]
    assert partial.headers["content-range"] == f"bytes 0-99/{len(data)}"
    tail = httpx.get(origin.url_for("/media/tiny.mp3"), headers={"Range": "bytes=-10"})
    assert tail.status_code == 206 and tail.content == data[-10:]
    open_ended = httpx.get(origin.url_for("/media/tiny.mp3"), headers={"Range": "bytes=100-"})
    assert open_ended.content == data[100:]
    bad = httpx.get(origin.url_for("/media/tiny.mp3"), headers={"Range": f"bytes={len(data)}-"})
    assert bad.status_code == 416 and bad.headers["content-range"] == f"bytes */{len(data)}"


def test_head_has_headers_but_no_body(origin: Origin) -> None:
    response = httpx.head(origin.url_for("/media/tiny.jpg"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert int(response.headers["content-length"]) > 0
    assert response.content == b""
    assert origin.requests[-1].method == "HEAD"


def test_scripted_responses_fire_n_times_then_the_file_returns(origin: Origin) -> None:
    origin.script("/rss/atom.xml", status=503, times=2, headers={"Retry-After": "1"})
    origin.script("/rss/atom.xml", status=304)
    codes = [httpx.get(origin.url_for("/rss/atom.xml")).status_code for _ in range(4)]
    assert codes == [503, 503, 304, 200]
    origin.script("/anything", status=200, body="<rss/>", content_type="application/rss+xml")
    canned = httpx.get(origin.url_for("/anything?x=1"))
    assert canned.text == "<rss/>" and canned.headers["content-type"] == "application/rss+xml"


def test_unknown_and_traversal_paths_are_404(origin: Origin) -> None:
    assert httpx.get(origin.url_for("/nope.xml")).status_code == 404
    assert httpx.get(origin.url_for("/../pyproject.toml")).status_code == 404
    assert httpx.get(origin.url_for("/rss/%2e%2e/%2e%2e/pyproject.toml")).status_code == 404
