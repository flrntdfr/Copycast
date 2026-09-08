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
    automatic = {"mode": "automatic", "latest_n": None, "retention_days": 7, "selection": None}
    assert empty.json() == {
        "language": None,
        "min_duration_seconds": None,
        "backfill": automatic,
        "refresh_interval_hours": 24,
    }

    stored = await client.put(
        api("/settings/defaults"),
        json={"language": " fr_ca ", "min_duration_seconds": 300, "backfill": {"mode": "all"}},
    )
    assert stored.status_code == 200, stored.text
    assert stored.json() == {
        "language": "fr-CA",
        "min_duration_seconds": 300,
        "backfill": {"mode": "all", "latest_n": None, "retention_days": None, "selection": None},
        "refresh_interval_hours": 24,
    }
    assert container.layout.defaults_path().is_file()
    assert (await client.get(api("/settings/defaults"))).json()["language"] == "fr-CA"

    for body in (
        {"language": "français"},
        {"min_duration_seconds": 0},
        {"bogus": 1},
        {"backfill": {"mode": "selection"}},
        {"backfill": {"mode": "rolling"}},
        {"refresh_interval_hours": 0},
    ):
        rejected = await client.put(api("/settings/defaults"), json=body)
        assert rejected.status_code == 422, body

    cleared = await client.put(api("/settings/defaults"), json={})
    assert cleared.json() == {
        "language": None,
        "min_duration_seconds": None,
        "backfill": automatic,
        "refresh_interval_hours": 24,
    }


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


async def test_youtube_playlists_list_the_account_and_mark_captures(
    client: httpx.AsyncClient, engine: FakeEngine
) -> None:
    from copycast.app import PLAYLISTS_URL

    mine = listing(
        2,
        service="YouTube",
        title="Playlists",
        items=[
            listing_item(
                1,
                key="YoutubeTab:PLabc",
                title="Talks",
                source_url="https://www.youtube.com/playlist?list=PLabc",
            ),
            listing_item(
                2,
                key="YoutubeTab:PLdef",
                title="Music",
                source_url="https://www.youtube.com/playlist?list=PLdef",
            ),
            listing_item(3, key="YoutubeTab:PLabc", title="Talks again", source_url=None),
        ],
    )
    engine.script_listing(PLAYLISTS_URL, mine)
    response = await client.get(api("/youtube/playlists"))
    assert response.status_code == 200, response.text
    playlists = response.json()["playlists"]
    assert [p["id"] for p in playlists] == ["WL", "PLabc", "PLdef"]
    assert playlists[0] == {
        "id": "WL",
        "title": "Watch Later",
        "url": "https://www.youtube.com/playlist?list=WL",
        "item_count": None,
        "captured_feed_id": None,
    }

    # Capturing a playlist marks it, and the Mirror carries the flags.
    engine.script_listing(
        "https://www.youtube.com/playlist?list=PLabc",
        listing(1, service="YouTube", title="Talks", raw={"_type": "playlist"}),
    )
    created = await client.post(
        api("/mirrors"),
        json={
            "source_url": "https://www.youtube.com/playlist?list=PLabc",
            "sync_deletions": True,
            "playlist_capture": True,
        },
    )
    assert created.status_code == 201, created.text
    mirror = created.json()
    assert mirror["playlist_capture"] is True and mirror["sync_deletions"] is True
    assert mirror["backfill"]["mode"] == "automatic"
    again = (await client.get(api("/youtube/playlists"))).json()["playlists"]
    assert next(p for p in again if p["id"] == "PLabc")["captured_feed_id"] == mirror["id"]
    assert next(p for p in again if p["id"] == "PLdef")["captured_feed_id"] is None
