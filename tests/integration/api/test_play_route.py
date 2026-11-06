"""The web player's routes (U10): ``/api/feeds/{f}/items/{i}/media`` and ``…/assets/{name}``.

``ItemMedia.play_url`` serves the archived object like ``/feeds`` does (Range, HEAD,
ETag) with a private cache policy, counts a play by the download rule, and names the
file after the item's title and the media's extension (``?download=1`` for an
attachment). ``AssetRead.play_url`` serves an asset of that item only, sandboxed unless
it is audio, video or a PDF. Sessions only: API keys are refused. Cross-user cases are
the isolation matrix's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from copycast.app import Container
from copycast.domain.enums import AssetKind, AssetState
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror
from tests.support.paths import api
from tests.support.world import World

pytestmark = pytest.mark.integration


def local(url: str) -> str:
    return url.removeprefix("http://testserver")


async def one_archived(
    client: httpx.AsyncClient, source: Source, runner: Runner, name: str, *, count: int = 1
) -> list[dict[str, Any]]:
    url = source.write_rss(name, items=list(range(count, 0, -1)), assets=True)
    mirror = await create_mirror(client, url)
    items = await archived_items(client, runner, mirror["id"])
    assert len(items) == count
    return sorted(items, key=lambda i: i["ordinal"])


async def downloads(client: httpx.AsyncClient, item: dict[str, Any]) -> int:
    fetched = (await client.get(api(f"/feeds/{item['feed_id']}/items/{item['id']}"))).json()
    count: int = fetched["download_count"]
    return count


async def test_media_streams_with_ranges_a_private_cache_and_a_file_name(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    (item,) = await one_archived(client, source, runner, "play")
    media = item["media"]
    feed_id, item_id, ext = item["feed_id"], item["id"], media["ext"]
    assert media["play_url"] == f"http://testserver/api/feeds/{feed_id}/items/{item_id}/media"
    path = local(media["play_url"])
    key = container.layout.media_key(feed_id, item_id, ext)
    stored: Any = container.layout.store.head(key)

    response = await client.get(path)
    assert response.status_code == 200, response.text
    assert response.content == container.layout.store.get_bytes(key)
    assert response.headers["content-type"] == media["mime"]
    assert response.headers["cache-control"] == "private, max-age=0"
    assert response.headers["etag"] == stored.etag
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-disposition"] == f'inline; filename="Episode 1.{ext}"'
    assert await downloads(client, item) == 1

    ranged = await client.get(path, headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206 and ranged.content == response.content[2:6]
    assert ranged.headers["content-range"] == f"bytes 2-5/{stored.size}"
    head = await client.head(path)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(stored.size)
    assert await downloads(client, item) == 1, "HEAD and later ranges never count"
    first = await client.get(path, headers={"Range": "bytes=0-1"})
    assert first.status_code == 206
    assert await downloads(client, item) == 2, "a range from byte 0 counts"

    download = await client.get(path, params={"download": "1"})
    assert download.status_code == 200
    assert download.headers["content-disposition"] == f'attachment; filename="Episode 1.{ext}"'


async def test_the_file_name_is_made_safe_and_keeps_a_unicode_title(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    (item,) = await one_archived(client, source, runner, "unicode")
    async with container.uow_factory() as uow:
        row = await uow.catalog.require(item["id"])
        row.title = 'Épisode / 1: "Q"\\'
    response = await client.get(local(item["media"]["play_url"]), params={"download": "true"})
    ext = item["media"]["ext"]
    name = f"Épisode 1 Q.{ext}"
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"pisode 1 Q.{ext}\"; filename*=UTF-8''{quote(name, safe='')}"
    )

    async with container.uow_factory() as uow:
        row = await uow.catalog.require(item["id"])
        row.title = "..."
    response = await client.get(local(item["media"]["play_url"]))
    assert response.headers["content-disposition"] == f'inline; filename="{item["id"]}.{ext}"'


async def test_an_item_without_media_is_404_and_never_archived_on_demand(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("lazy", items=[1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    (item,) = (await client.get(api(f"/feeds/{mirror['id']}/items"))).json()["items"]
    assert item["media"] is None
    path = api(f"/feeds/{mirror['id']}/items/{item['id']}/media")
    response = await client.get(path)
    assert response.status_code == 404
    assert response.json()["type"] == "urn:copycast:problem:not-found"
    after = (await client.get(api(f"/feeds/{mirror['id']}/items/{item['id']}"))).json()
    assert after["state"] != "wanted"
    assert (await client.get(api(f"/feeds/{mirror['id']}/items/nope/media"))).status_code == 404


async def test_assets_are_served_for_their_own_item_only(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    first, second = await one_archived(client, source, runner, "assets", count=2)
    chapters = next(a for a in first["assets"] if a["kind"] == "chapters")
    assert chapters["play_url"].endswith(
        f"/api/feeds/{first['feed_id']}/items/{first['id']}/assets/{chapters['name']}"
    )
    response = await client.get(local(chapters["play_url"]))
    assert response.status_code == 200, response.text
    assert response.json()["chapters"]
    assert response.headers["cache-control"] == "private, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"

    # The same name under the other item of the same Feed: not its asset.
    other = api(f"/feeds/{second['feed_id']}/items/{second['id']}/assets/{chapters['name']}")
    assert (await client.get(other)).status_code == 404
    missing = api(f"/feeds/{first['feed_id']}/items/{first['id']}/assets/nope.json")
    assert (await client.get(missing)).status_code == 404


async def test_an_html_asset_is_sandboxed(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    (item,) = await one_archived(client, source, runner, "html")
    feed_id, item_id = item["feed_id"], item["id"]
    layout = container.layout
    key = layout.attachment_key(feed_id, item_id, "0123456789ab", "html")
    body = b"<script>alert(document.cookie)</script>"
    layout.store.put_bytes(key, body, content_type="text/html")
    async with container.uow_factory() as uow:
        await uow.assets.upsert(
            feed_id,
            item_id,
            AssetKind.attachment,
            local_path=layout.relative(feed_id, key),
            mime="text/html",
            size_bytes=len(body),
            state=AssetState.archived,
            fetched_at=datetime.now(UTC),
            slot="0123456789ab",
        )
    fetched = (await client.get(api(f"/feeds/{feed_id}/items/{item_id}"))).json()
    html = next(a for a in fetched["assets"] if a["kind"] == "attachment")
    response = await client.get(local(html["play_url"]))
    assert response.status_code == 200 and response.content == body
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == "sandbox"


async def test_sessions_play_and_api_keys_are_refused(
    world: World, bob_client: httpx.AsyncClient
) -> None:
    item = (await bob_client.get(api(f"/feeds/{world.bucket_id}/items/{world.item_id}"))).json()
    path = local(item["media"]["play_url"])
    played = await bob_client.get(path)
    assert played.status_code == 200, played.text
    assert played.headers["content-disposition"].startswith('inline; filename="Bob')

    bearer = {"Authorization": f"Bearer {world.key_secret}"}
    async with httpx.AsyncClient(
        transport=bob_client._transport,  # pyright: ignore[reportPrivateUsage]
        base_url="http://testserver",
    ) as anonymous:
        refused = await anonymous.get(path, headers=bearer)
        assert refused.status_code == 403, refused.text
        assert (await anonymous.get(path)).status_code == 401
