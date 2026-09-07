"""The preview route and an Automatic Mirror's feed and on-demand media."""

from __future__ import annotations

import httpx
import pytest
from lxml import etree

from copycast.adapters.api.routes import public
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, create_mirror, items_of
from tests.support.paths import MEDIA_URL, api

pytestmark = pytest.mark.integration


async def test_preview_counts_without_changing_anything(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("preview", items=[3, 2, 1], artwork=False)
    mirror = await create_mirror(client, url)
    await runner.run_until_idle()
    response = await client.post(
        api(f"/mirrors/{mirror['id']}/preview"),
        json={"backfill": {"mode": "rolling", "latest_n": 2}},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["would_delete_count"] == 1 and preview["would_delete_bytes"] > 0
    assert preview["would_archive_count"] == 0
    assert len(await items_of(client, mirror["id"], state="archived")) == 3
    empty = await client.post(api(f"/mirrors/{mirror['id']}/preview"), json={"follow": False})
    assert empty.json() == {
        "would_delete_count": 0,
        "would_delete_bytes": 0,
        "would_archive_count": 0,
    }
    assert (await client.post(api("/mirrors/nope/preview"), json={})).status_code == 404


async def test_automatic_feed_lists_everything_and_archives_on_request(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = source.write_rss("ondemand", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    assert mirror["backfill"] == {"mode": "automatic", "latest_n": None, "retention_days": 7}
    await runner.run_until_idle()
    assert await items_of(client, mirror["id"], state="archived") == []

    # The feed lists both items with placeholder enclosures.
    feed = await client.get(f"/feeds/{mirror['id']}.xml")
    assert feed.status_code == 200
    root = etree.fromstring(feed.content)
    enclosures = root.findall(".//item/enclosure")
    assert len(enclosures) == 2
    assert {e.get("type") for e in enclosures} == {"audio/mpeg"}
    assert {e.get("length") for e in enclosures} == {"0"}
    urls = [e.get("url") or "" for e in enclosures]
    assert all(u.endswith(".mp3") for u in urls)

    # The worker is not running: the request waits (shortened here) then says retry.
    monkeypatch.setattr(public, "ON_DEMAND_WAIT_SECONDS", 0.0)
    items = await items_of(client, mirror["id"])
    newest = next(i for i in items if i["source_number"] == 2)
    first = await client.get(MEDIA_URL(mirror["id"], newest["id"], "mp3"))
    assert first.status_code == 503, first.text
    assert first.headers["retry-after"] == "30"
    assert first.headers["content-type"].startswith("application/problem+json")
    # ...but the archive was queued and completes in the background.
    await runner.run_until_idle()
    archived = await items_of(client, mirror["id"], state="archived")
    assert [i["id"] for i in archived] == [newest["id"]]
    again = await client.get(MEDIA_URL(mirror["id"], newest["id"], "mp3"))
    assert again.status_code == 200 and again.headers["content-type"] == "audio/mp4"
    # The feed now carries the real enclosure for that item and the placeholder for the other.
    feed = await client.get(f"/feeds/{mirror['id']}.xml")
    types = sorted(
        e.get("type") or "" for e in etree.fromstring(feed.content).findall(".//enclosure")
    )
    assert types == ["audio/mp4", "audio/mpeg"]
    # A non-Automatic Mirror keeps 404ing unknown extensions (test_media covers it).
    other = await client.get(MEDIA_URL(mirror["id"], "0123456789abcdef", "mp3"))
    assert other.status_code == 404
