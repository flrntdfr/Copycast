"""Media and asset routes: Range, ETag, traversal, the download-counting table."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror
from tests.support.paths import ASSET_URL, MEDIA_URL, api

pytestmark = pytest.mark.integration


async def one_archived(
    client: httpx.AsyncClient, source: Source, runner: Runner, *, name: str, assets: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = source.write_rss(name, items=[1], artwork=assets, assets=assets)
    mirror = await create_mirror(client, url)
    items = await archived_items(client, runner, mirror["id"])
    assert len(items) == 1
    return mirror, items[0]


async def download_count(client: httpx.AsyncClient, feed_id: str, item_id: str) -> int:
    item = (await client.get(api(f"/feeds/{feed_id}/items/{item_id}"))).json()
    count: int = item["download_count"]
    return count


def media_path(item: dict[str, Any]) -> str:
    return MEDIA_URL(item["feed_id"], item["id"], item["media"]["ext"])


async def test_full_get_serves_the_file_and_counts_once(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    mirror, item = await one_archived(client, source, runner, name="full")
    assert item["media"]["url"] == "http://testserver" + media_path(item)
    response = await client.get(media_path(item))
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mp4"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"].startswith('"')  # strong
    assert len(response.content) == item["media"]["bytes"]
    assert await download_count(client, mirror["id"], item["id"]) == 1
    fetched = (await client.get(api(f"/feeds/{mirror['id']}/items/{item['id']}"))).json()
    assert fetched["first_downloaded_at"] and fetched["last_downloaded_at"]


@pytest.mark.parametrize(
    ("method", "range_header", "status", "counted"),
    [
        ("GET", None, 200, 1),
        ("GET", "bytes=0-9", 206, 1),
        ("GET", "bytes=0-", 206, 1),
        ("GET", "bytes=5-", 206, 0),
        ("GET", "bytes=10-20", 206, 0),
        ("HEAD", None, 200, 0),
        ("HEAD", "bytes=0-9", 206, 0),
    ],
)
async def test_download_counting_table(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    method: str,
    range_header: str | None,
    status: int,
    counted: int,
) -> None:
    mirror, item = await one_archived(client, source, runner, name=f"count-{method}-{counted}")
    headers = {"Range": range_header} if range_header else {}
    response = await client.request(method, media_path(item), headers=headers)
    assert response.status_code == status, response.text
    if status == 206:
        assert response.headers["content-range"].startswith("bytes ")
    if method == "HEAD":
        assert response.content == b""
    assert await download_count(client, mirror["id"], item["id"]) == counted


async def test_unsatisfiable_range_is_416(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    mirror, item = await one_archived(client, source, runner, name="range416")
    size = item["media"]["bytes"]
    response = await client.get(media_path(item), headers={"Range": f"bytes={size + 10}-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{size}"
    assert await download_count(client, mirror["id"], item["id"]) == 0


async def test_media_404s(client: httpx.AsyncClient, source: Source, runner: Runner) -> None:
    mirror, item = await one_archived(client, source, runner, name="404s")
    problem = "application/problem+json"
    wrong_ext = await client.get(MEDIA_URL(mirror["id"], item["id"], "mp3"))
    assert wrong_ext.status_code == 404 and wrong_ext.headers["content-type"].startswith(problem)
    unknown_item = await client.get(MEDIA_URL(mirror["id"], "0123456789abcdef", "m4a"))
    assert unknown_item.status_code == 404
    unknown_feed = await client.get(MEDIA_URL("nope", item["id"], "m4a"))
    assert unknown_feed.status_code == 404
    for traversal in ("..%2F..%2Ffeed.json", "..", "%2e%2e/feed.json", "feed.json"):
        response = await client.get(f"/feeds/{mirror['id']}/media/{traversal}")
        assert response.status_code == 404, traversal
    assert await download_count(client, mirror["id"], item["id"]) == 0


async def test_assets_are_served_and_never_counted(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    mirror, item = await one_archived(client, source, runner, name="assets", assets=True)
    served = [a for a in item["assets"] if a["url"]]
    kinds = {a["kind"] for a in served}
    assert {"chapters", "transcript"} <= kinds, item["assets"]
    for asset in served:
        assert asset["url"].startswith(f"http://testserver{ASSET_URL(mirror['id'], '')}")
        basename = asset["url"].rsplit("/", 1)[-1]
        response = await client.get(ASSET_URL(mirror["id"], basename))
        assert response.status_code == 200, asset
        assert response.headers["cache-control"] == "public, max-age=86400"
        if asset["kind"] == "chapters":
            assert response.headers["content-type"].startswith("application/json")
            assert b"Intro" in response.content
        head = await client.head(ASSET_URL(mirror["id"], basename))
        assert head.status_code == 200 and head.content == b""
    assert await download_count(client, mirror["id"], item["id"]) == 0

    missing = await client.get(ASSET_URL(mirror["id"], "nothing.json"))
    assert missing.status_code == 404
    for traversal in ("..%2Ffeed.json", ".hidden", "a%2Fb.json"):
        assert (await client.get(f"/feeds/{mirror['id']}/assets/{traversal}")).status_code == 404
    other = (await client.get(api("/inboxes"))).json()["feeds"][0]
    basename = served[0]["url"].rsplit("/", 1)[-1]
    assert (await client.get(ASSET_URL(other["id"], basename))).status_code == 404


async def test_deleted_item_media_is_gone(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    mirror, item = await one_archived(client, source, runner, name="deleted")
    assert (await client.get(media_path(item))).status_code == 200
    deleted = await client.delete(api(f"/feeds/{mirror['id']}/items/{item['id']}"))
    assert deleted.status_code == 204
    assert (await client.get(media_path(item))).status_code == 404
    row = (await client.get(api(f"/feeds/{mirror['id']}/items/{item['id']}"))).json()
    assert row["state"] == "deleted" and row["media"] is None and row["listed"] is True
