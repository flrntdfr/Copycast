from __future__ import annotations

import pytest

from copycast.adapters.sources.http import (
    USER_AGENT,
    BodyTooLarge,
    SourceRejected,
    SourceUnreachable,
    close_client,
    content_type_of,
    create_client,
    fetch,
    get_client,
)
from copycast.version import APP_VERSION
from tests.support.origin import Origin


def test_user_agent_names_the_version() -> None:
    assert f"Copycast/{APP_VERSION} (+https://github.com/flrntdfr/copycast)" == USER_AGENT
    client = create_client()
    assert client.headers["User-Agent"] == USER_AGENT
    client.close()


def test_shared_client_is_reused_and_recreated_after_close() -> None:
    first = get_client()
    assert get_client() is first
    close_client()
    assert first.is_closed
    second = get_client()
    assert second is not first
    close_client()


def test_fetch_reads_body_headers_and_sends_the_user_agent(origin: Origin) -> None:
    fetched = fetch(origin.url_for("/media/tiny.jpg"), max_bytes=1_000_000)
    assert fetched.status == 200
    assert fetched.content_type == "image/jpeg"
    assert fetched.etag and fetched.last_modified
    assert fetched.body.startswith(b"\xff\xd8")
    assert not fetched.not_modified
    assert origin.requests[-1].headers["user-agent"] == USER_AGENT


def test_conditional_requests(origin: Origin) -> None:
    first = fetch(origin.url_for("/rss/atom.xml"), max_bytes=10_000)
    again = fetch(origin.url_for("/rss/atom.xml"), max_bytes=10_000, etag=first.etag)
    assert again.not_modified and again.status == 304 and again.body == b""
    since = fetch(
        origin.url_for("/rss/atom.xml"), max_bytes=10_000, last_modified=first.last_modified
    )
    assert since.not_modified
    assert origin.requests[-1].headers["if-modified-since"] == first.last_modified


def test_body_cap_from_content_length_and_from_streaming(origin: Origin) -> None:
    with pytest.raises(BodyTooLarge):
        fetch(origin.url_for("/media/tiny.mp3"), max_bytes=100)
    origin.script("/chunked", body=b"x" * 5000, headers={"Transfer-Encoding": "chunked"})
    with pytest.raises(BodyTooLarge):
        fetch(origin.url_for("/chunked"), max_bytes=100)


def test_status_mapping(origin: Origin) -> None:
    origin.script("/gone", status=404)
    with pytest.raises(SourceRejected) as rejected:
        fetch(origin.url_for("/gone"), max_bytes=10)
    assert rejected.value.status == 404
    origin.script("/down", status=503)
    with pytest.raises(SourceUnreachable):
        fetch(origin.url_for("/down"), max_bytes=10)
    with pytest.raises(SourceUnreachable):
        fetch("http://127.0.0.1:9/nothing", max_bytes=10)


def test_head_and_accept(origin: Origin) -> None:
    head = fetch(origin.url_for("/media/tiny.mp3"), max_bytes=10, method="HEAD", accept="audio/*")
    assert head.body == b"" and head.content_type == "audio/mpeg"
    assert origin.requests[-1].method == "HEAD"
    assert origin.requests[-1].headers["accept"] == "audio/*"


def test_content_type_of(origin: Origin) -> None:
    assert content_type_of(origin.url_for("/media/tiny.mp3")) == "audio/mpeg"
    assert content_type_of(origin.url_for("/missing")) is None
    assert content_type_of("http://127.0.0.1:9/x") is None
