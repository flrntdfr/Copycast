"""The storage view of a Feed: ``list_feed_objects`` and the ``download_feed_object`` route."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from copycast.app import Container
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror
from tests.support.paths import api

pytestmark = pytest.mark.integration


async def listing_of(client: httpx.AsyncClient, feed_id: str) -> dict[str, Any]:
    response = await client.get(api(f"/feeds/{feed_id}/objects"))
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def by_kind(listing: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [obj for obj in listing["objects"] if obj["kind"] == kind]


async def test_lists_every_object_of_a_feed_by_kind_with_titles(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("objects", items=[2, 1], artwork=True, assets=True)
    mirror = await create_mirror(client, url)
    items = await archived_items(client, runner, mirror["id"])
    assert len(items) == 2

    listing = await listing_of(client, mirror["id"])
    assert listing["feed_id"] == mirror["id"]
    assert listing["count"] == len(listing["objects"]) > 0
    assert listing["total_bytes"] == sum(obj["size_bytes"] for obj in listing["objects"])

    media = by_kind(listing, "media")
    assert {obj["item_title"] for obj in media} == {"Episode 1", "Episode 2"}
    for obj in media:
        assert obj["path"] == f"media/{obj['name']}" and obj["item_id"] in {i["id"] for i in items}
        expected = api(f"/feeds/{mirror['id']}/objects/{obj['path']}")
        assert obj["url"] == f"http://testserver{expected}"
        assert obj["size_bytes"] > 0 and obj["last_modified"]
    artwork = by_kind(listing, "artwork")
    assert artwork and all(obj["path"].startswith("assets/") for obj in artwork)
    assert {obj["kind"] for obj in listing["objects"]} >= {
        "media",
        "artwork",
        "chapters",
        "transcript",
        "descriptor",
        "source",
    }
    assert [obj["path"] for obj in by_kind(listing, "descriptor")] == ["feed.json"]
    assert "source/feed.xml" in {obj["path"] for obj in by_kind(listing, "source")}
    sidecars = by_kind(listing, "sidecar")
    assert sidecars and all(obj["item_id"] is None for obj in sidecars)

    # Media first, then the assets, then descriptor and Source copies, then the sidecars.
    kinds = [obj["kind"] for obj in listing["objects"]]
    order = ["media", "artwork", "chapters", "transcript", "attachment", "descriptor", "source"]
    order += ["sidecar", "other"]
    assert kinds == sorted(kinds, key=order.index)


async def test_download_streams_the_object_as_an_attachment(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("download", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    await archived_items(client, runner, mirror["id"])
    listing = await listing_of(client, mirror["id"])
    media = by_kind(listing, "media")[0]

    response = await client.get(media["url"])
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="{media["name"]}"'
    assert response.headers["content-type"] == "audio/mp4"
    key = container.layout.resolve(mirror["id"], media["path"])
    assert response.content == container.layout.store.get_bytes(key)
    assert len(response.content) == media["size_bytes"]

    descriptor = await client.get(api(f"/feeds/{mirror['id']}/objects/feed.json"))
    assert descriptor.status_code == 200
    assert descriptor.headers["content-type"].startswith("application/json")
    assert isinstance(descriptor.json(), dict) and mirror["id"] in descriptor.text


async def test_download_refuses_traversal_and_unknowns(
    client: httpx.AsyncClient, source: Source
) -> None:
    url = source.write_rss("traversal", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    # Encoded so the client sends them verbatim (httpx resolves a literal ``..`` itself).
    traversals = (
        "%2e%2e",
        "..%2F..%2Ffeed.json",
        "%2e%2e/feed.json",
        "media/%2e%2e/LAYOUT_VERSION",
    )
    for path in traversals:
        response = await client.get(api(f"/feeds/{mirror['id']}/objects/{path}"))
        assert response.status_code == 404, path
        assert response.headers["content-type"].startswith("application/problem+json")
    missing = await client.get(api(f"/feeds/{mirror['id']}/objects/media/none.m4a"))
    assert missing.status_code == 404
    unknown_feed = await client.get(api("/feeds/nope/objects/feed.json"))
    assert unknown_feed.status_code == 404
    unknown_listing = await client.get(api("/feeds/nope/objects"))
    assert unknown_listing.status_code == 404


async def test_download_names_a_non_ascii_object_safely(
    client: httpx.AsyncClient, source: Source, container: Container
) -> None:
    """A foreign name under the feed prefix: an ASCII ``filename`` plus RFC 5987's ``filename*``."""
    url = source.write_rss("foreign", items=[1], artwork=False)
    mirror = await create_mirror(client, url)
    key = container.layout.feed_prefix(mirror["id"]) + 'other/épisode "final".txt'
    container.layout.store.put_bytes(key, b"hello", content_type="text/plain")
    listing = await listing_of(client, mirror["id"])
    other = by_kind(listing, "other")
    assert [obj["name"] for obj in other] == ['épisode "final".txt']
    response = await client.get(other[0]["url"])
    assert response.status_code == 200, response.text
    assert response.content == b"hello"
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"pisode final.txt\"; filename*=utf-8''%C3%A9pisode%20%22final%22.txt"
    )
