"""A first request for an Automatic feed's Episode holds the line and gets the finished object.

The worker runs while the request waits; the wait is woken by the item's ``archived``
event (the poll interval is pushed past the test's patience, so only the event can wake
it in time). Bytes, length and ETag are the Object store object's: bit-perfect, tags included.
Also the proxy's multi-range and ``If-Range`` answers, which match ``FileResponse``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from copycast.adapters.api.routes import public
from copycast.app import Container
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror, items_of

pytestmark = pytest.mark.integration

HOLD_PATIENCE_SECONDS = 10.0


def local(url: str) -> str:
    return url.removeprefix("http://testserver")


async def test_the_first_request_holds_the_line_and_serves_the_finished_object(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(public, "ON_DEMAND_POLL_SECONDS", 30.0)
    url = source.write_rss("hold", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    first, second = sorted(await items_of(client, mirror["id"]), key=lambda i: i["ordinal"])
    assert first["media"] is None and second["media"] is None

    task = asyncio.create_task(runner.run())
    try:
        started = time.monotonic()
        response = await client.get(local(first["public_media_url"]))
        assert time.monotonic() - started < HOLD_PATIENCE_SECONDS, "woken by the event"
        assert response.status_code == 200, response.text
        layout = container.layout
        archived = {i["id"]: i for i in await items_of(client, mirror["id"], state="archived")}
        key = layout.media_key(mirror["id"], first["id"], archived[first["id"]]["media"]["ext"])
        stored: Any = layout.store.head(key)
        assert response.content == layout.store.get_bytes(key)
        assert response.headers["content-length"] == str(stored.size)
        assert response.headers["etag"] == stored.etag
        assert response.headers["accept-ranges"] == "bytes"

        # A HEAD holds the line the same way: its Content-Length is the finished object's.
        started = time.monotonic()
        head = await client.head(local(second["public_media_url"]))
        assert time.monotonic() - started < HOLD_PATIENCE_SECONDS
        assert head.status_code == 200 and head.content == b""
        archived = {i["id"]: i for i in await items_of(client, mirror["id"], state="archived")}
        assert head.headers["content-length"] == str(archived[second["id"]]["media"]["bytes"])
    finally:
        runner.request_stop()
        await asyncio.wait_for(task, timeout=15)


async def test_the_proxy_answers_ranges_like_a_file(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("ranges", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    (item,) = await archived_items(client, runner, mirror["id"])
    path = local(item["media"]["url"])
    body = (await client.get(path)).content
    size = len(body)
    assert size == item["media"]["bytes"] > 20

    single = await client.get(path, headers={"Range": "bytes=-10"})
    assert single.status_code == 206 and single.content == body[-10:]
    assert single.headers["content-range"] == f"bytes {size - 10}-{size - 1}/{size}"

    multi = await client.get(path, headers={"Range": "bytes=0-1,5-7"})
    assert multi.status_code == 206
    assert multi.headers["content-type"].startswith("multipart/byteranges; boundary=")
    assert int(multi.headers["content-length"]) == len(multi.content)
    assert body[0:2] in multi.content and body[5:8] in multi.content

    etag = single.headers["etag"]
    matching = await client.get(path, headers={"Range": "bytes=2-3", "If-Range": etag})
    assert matching.status_code == 206 and matching.content == body[2:4]
    stale = await client.get(path, headers={"Range": "bytes=2-3", "If-Range": '"stale"'})
    assert stale.status_code == 200 and stale.content == body

    malformed = await client.get(path, headers={"Range": "items=0-1"})
    assert malformed.status_code == 400
    past = await client.get(path, headers={"Range": f"bytes={size}-"})
    assert past.status_code == 416 and past.headers["content-range"] == f"bytes */{size}"

    # The object vanished from the Object store behind the row's back: 404, not a 500.
    store = container.layout.store
    store.delete(container.layout.media_key(mirror["id"], item["id"], item["media"]["ext"]))
    assert (await client.get(path)).status_code == 404
