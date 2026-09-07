"""Operator defaults (``/api/settings/defaults``) and video search (``/api/search/videos``)."""

from __future__ import annotations

import httpx
import pytest

from copycast.app import Container
from tests.support.factories import EPOCH, listing, listing_item
from tests.support.fake_engine import FakeEngine
from tests.support.paths import api

pytestmark = pytest.mark.integration


async def test_defaults_round_trip_and_validation(
    client: httpx.AsyncClient, container: Container
) -> None:
    empty = await client.get(api("/settings/defaults"))
    assert empty.status_code == 200
    assert empty.json() == {"language": None, "min_duration_seconds": None}

    stored = await client.put(
        api("/settings/defaults"), json={"language": " fr_ca ", "min_duration_seconds": 300}
    )
    assert stored.status_code == 200, stored.text
    assert stored.json() == {"language": "fr-CA", "min_duration_seconds": 300}
    assert container.layout.defaults_path().is_file()
    assert (await client.get(api("/settings/defaults"))).json()["language"] == "fr-CA"

    for body in ({"language": "français"}, {"min_duration_seconds": 0}, {"bogus": 1}):
        rejected = await client.put(api("/settings/defaults"), json=body)
        assert rejected.status_code == 422, body

    cleared = await client.put(api("/settings/defaults"), json={})
    assert cleared.json() == {"language": None, "min_duration_seconds": None}


async def test_search_videos_lists_engine_hits(
    client: httpx.AsyncClient, engine: FakeEngine
) -> None:
    hits = [
        listing_item(
            1,
            title="WWDC 2024 Live from Cupertino",
            source_url="https://www.youtube.com/watch?v=abc123",
            author="The Talk Show",
            duration_seconds=5400,
            published_at=EPOCH,
        ),
        listing_item(2, title="No URL", source_url=None),
    ]
    engine.script_listing(
        "ytsearch5:gruber wwdc", listing(2, service="YouTube", title="search", items=hits)
    )
    response = await client.get(
        api("/search/videos"), params={"query": "  gruber   wwdc ", "limit": 5}
    )
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["query"] == "gruber wwdc"
    assert [r["url"] for r in page["results"]] == ["https://www.youtube.com/watch?v=abc123"]
    first = page["results"][0]
    assert first["title"] == "WWDC 2024 Live from Cupertino"
    assert first["channel"] == "The Talk Show" and first["duration_seconds"] == 5400
    assert first["published_at"] is not None

    blank = await client.get(api("/search/videos"), params={"query": "   "})
    assert blank.status_code == 200 and blank.json()["results"] == []
