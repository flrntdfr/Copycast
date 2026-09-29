"""The preview route and an Automatic Mirror's feed and on-demand media."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from lxml import etree

from copycast.adapters.api.routes import public
from copycast.app import Container
from copycast.domain.enums import LiveStatus
from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, create_mirror, items_of
from tests.integration.api.test_feeds_public import CHANNEL_RAW, outside_cooldown
from tests.support.factories import EPOCH, listing, listing_item
from tests.support.fake_engine import FakeEngine
from tests.support.paths import FEED_URL, MEDIA_URL, api

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


def enclosures_of(feed: httpx.Response) -> list[Any]:
    assert feed.status_code == 200, feed.text
    return etree.fromstring(feed.content).findall(".//item/enclosure")


async def test_automatic_feed_lists_everything_under_stable_urls_and_archives_on_request(
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

    # The feed lists both items under the URL they keep for good: the RSS enclosures say
    # audio/mpeg, so ".mp3", whatever container the archive gets later.
    enclosures = enclosures_of(await client.get(f"/feeds/{mirror['id']}.xml"))
    assert len(enclosures) == 2
    assert {e.get("type") for e in enclosures} == {"audio/mpeg"}
    # Unarchived items claim 128 kbit/s worth of bytes for their duration (60 s and 120 s here).
    assert {e.get("length") for e in enclosures} == {"960000", "1920000"}
    advertised = {e.get("url") or "" for e in enclosures}
    assert all(u.endswith(".mp3") for u in advertised)
    items = await items_of(client, mirror["id"])
    assert {i["public_media_url"] for i in items} == advertised
    assert all(i["media"] is None for i in items)

    # The worker is not running: the request waits (shortened here) then says retry.
    monkeypatch.setattr(public, "ON_DEMAND_WAIT_SECONDS", 0.0)
    newest = next(i for i in items if i["source_number"] == 2)
    stable = MEDIA_URL(mirror["id"], newest["id"], "mp3")
    assert newest["public_media_url"] == "http://testserver" + stable
    first = await client.get(stable)
    assert first.status_code == 503, first.text
    assert first.headers["retry-after"] == "30"
    assert first.headers["content-type"].startswith("application/problem+json")
    # ...but the archive was queued and completes in the background.
    await runner.run_until_idle()
    archived = await items_of(client, mirror["id"], state="archived")
    assert [i["id"] for i in archived] == [newest["id"]]
    # The FakeEngine archives m4a: the stable URL serves the file under its real type, and
    # so does the file's own name; any other extension is unknown.
    assert archived[0]["media"]["ext"] == "m4a"
    assert archived[0]["public_media_url"] == newest["public_media_url"]
    again = await client.get(stable)
    assert again.status_code == 200 and again.headers["content-type"] == "audio/mp4"
    real = await client.get(MEDIA_URL(mirror["id"], newest["id"], "m4a"))
    assert real.status_code == 200 and real.headers["content-type"] == "audio/mp4"
    assert (await client.get(MEDIA_URL(mirror["id"], newest["id"], "ogg"))).status_code == 404
    # The feed advertises the very same URLs; only the archived item's type is now the file's.
    enclosures = enclosures_of(await client.get(f"/feeds/{mirror['id']}.xml"))
    assert {e.get("url") or "" for e in enclosures} == advertised
    assert sorted(e.get("type") or "" for e in enclosures) == ["audio/mp4", "audio/mpeg"]
    # An unknown item 404s whatever the extension.
    other = await client.get(MEDIA_URL(mirror["id"], "0123456789abcdef", "mp3"))
    assert other.status_code == 404

    # A deleted (or expired) Episode stays listed under the same URL and downloads again.
    gone = await client.delete(api(f"/feeds/{mirror['id']}/items/{newest['id']}"))
    assert gone.status_code == 204
    enclosures = enclosures_of(await client.get(f"/feeds/{mirror['id']}.xml"))
    assert {e.get("url") or "" for e in enclosures} == advertised
    assert {e.get("type") for e in enclosures} == {"audio/mpeg"}
    retry = await client.get(stable)
    assert retry.status_code == 503
    await runner.run_until_idle()
    assert (await client.get(stable)).status_code == 200


async def test_automatic_feed_lists_a_live_stream_only_once_its_recording_is_published(
    client: httpx.AsyncClient,
    engine: FakeEngine,
    runner: Runner,
    container: Container,
) -> None:
    """The Catalog shows the stream (with its status), the feed does not; an on-demand
    archive is refused; the light Refresh a fetch runs is enough to list it afterwards."""
    url = "https://www.youtube.com/@live/streams"
    plain = listing_item(1, published_at=EPOCH + timedelta(days=1), position=1, source_number=1)
    live = listing_item(
        2,
        published_at=EPOCH + timedelta(days=2),
        position=0,
        source_number=2,
        live_status=LiveStatus.is_live,
        archivable=False,
    )
    engine.script_listing(
        url, listing(2, service="YouTube", title="Live", raw=CHANNEL_RAW, items=[live, plain])
    )
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    items = {i["source_number"]: i for i in await items_of(client, mirror["id"])}
    assert items[2]["live_status"] == "is_live" and items[1]["live_status"] is None
    assert items[2]["state"] == "available" and items[2]["media"] is None
    enclosures = enclosures_of(await client.get(f"/feeds/{mirror['id']}.xml"))
    assert [e.get("url") for e in enclosures] == [items[1]["public_media_url"]]

    refused = await client.post(api(f"/feeds/{mirror['id']}/items/{items[2]['id']}/archive"))
    assert refused.status_code == 422, refused.text
    assert refused.json()["type"] == "urn:copycast:problem:source-unsupported"

    # The stream ended: the first page a fetch checks says so, and the feed lists it.
    await outside_cooldown(container, mirror["id"])
    recorded = live.model_copy(update={"live_status": LiveStatus.was_live, "archivable": True})
    engine.script_listing(
        url,
        listing(2, service="YouTube", title="Live", raw=CHANNEL_RAW, items=[recorded]),
    )
    response = await client.get(FEED_URL(mirror["id"]))
    assert response.status_code == 200
    assert engine.records.listings[-1].limit is not None, "a shallow (partial) listing"
    enclosures = enclosures_of(response)
    assert {e.get("url") for e in enclosures} == {
        items[1]["public_media_url"],
        items[2]["public_media_url"],
    }
    items = {i["source_number"]: i for i in await items_of(client, mirror["id"])}
    assert items[2]["live_status"] == "was_live" and items[2]["listed"] is True
    queued = await client.post(api(f"/feeds/{mirror['id']}/items/{items[2]['id']}/archive"))
    assert queued.status_code == 202, queued.text


async def test_automatic_ytdlp_feed_advertises_m4a_and_still_serves_the_legacy_mp3_url(
    client: httpx.AsyncClient,
    engine: FakeEngine,
    runner: Runner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://www.youtube.com/@ondemand/videos"
    engine.script_listing(url, listing(2, service="YouTube", title="Tube"))
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    items = await items_of(client, mirror["id"])
    assert len(items) == 2 and all(i["public_media_url"].endswith(".m4a") for i in items)
    enclosures = enclosures_of(await client.get(f"/feeds/{mirror['id']}.xml"))
    assert {e.get("type") for e in enclosures} == {"audio/mp4"}
    assert {e.get("url") for e in enclosures} == {i["public_media_url"] for i in items}

    # An app that subscribed before 1.3 holds "{id}.mp3": it still archives and serves.
    monkeypatch.setattr(public, "ON_DEMAND_WAIT_SECONDS", 0.0)
    newest = next(i for i in items if i["source_number"] == 2)
    legacy = MEDIA_URL(mirror["id"], newest["id"], "mp3")
    assert (await client.get(legacy)).status_code == 503
    await runner.run_until_idle()
    served = await client.get(legacy)
    assert served.status_code == 200 and served.headers["content-type"] == "audio/mp4"
    assert (await client.get(MEDIA_URL(mirror["id"], newest["id"], "m4a"))).status_code == 200
    assert (await client.get(MEDIA_URL(mirror["id"], newest["id"], "ogg"))).status_code == 404
