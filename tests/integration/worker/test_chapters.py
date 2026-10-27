"""Engine chapters are archived as Podcasting 2.0 JSON, and the worker upgrades older files."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import structlog
from lxml import etree

from copycast.adapters.sources.rss import NS_PODCAST, tag
from copycast.app import Container
from copycast.domain.enums import ArchiveState, AssetFormat, AssetKind, AssetState
from copycast.worker import main as worker_main
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import create_mirror, uow
from tests.support.factories import listing, listing_item
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration

CHANNEL = "https://www.youtube.com/@chaptered/videos"
CHAPTERS_MIME = "application/json+chapters"
# yt-dlp's shape, out of order, with a chapter that has no usable start.
RAW_CHAPTERS: list[dict[str, Any]] = [
    {"start_time": 63.0, "end_time": 125.25, "title": "The interview "},
    {"start_time": 0.0, "end_time": 63.0, "title": "Intro"},
    {"start_time": None, "end_time": 130.0, "title": "Broken"},
]
PODCAST_CHAPTERS = {
    "version": "1.2.0",
    "chapters": [
        {"startTime": 0, "endTime": 63, "title": "Intro"},
        {"startTime": 63, "endTime": 125.25, "title": "The interview"},
    ],
}


def _watch_url(n: int) -> str:
    return f"https://www.youtube.com/watch?v=chaptered{n:02d}"


async def _archived_channel(
    container: Container, runner: Runner, engine: FakeEngine
) -> tuple[str, dict[int, str]]:
    """A YouTube-like Mirror of two videos, both archived; only video 2 has chapters."""
    videos = [
        listing_item(
            n, source_url=_watch_url(n), position=2 - n, source_number=n, duration_seconds=900
        )
        for n in (1, 2)
    ]
    engine.script_listing(
        CHANNEL, listing(2, service="YouTube", raw={"_type": "playlist"}, items=videos)
    )
    engine.script_chapters(_watch_url(2), RAW_CHAPTERS)
    mirror = await create_mirror(container, CHANNEL)
    await runner.run_until_idle()
    async with uow(container) as unit:
        rows = await unit.catalog.for_feed(mirror.id)
    assert {row.archive_state for row in rows} == {ArchiveState.archived}
    return mirror.id, {row.source_number or 0: row.id for row in rows}


async def _advertised(client: httpx.AsyncClient, feed_id: str) -> list[Any]:
    response = await client.get(f"/feeds/{feed_id}.xml")
    assert response.status_code == 200, response.text
    return etree.fromstring(response.content).findall(f".//item/{tag(NS_PODCAST, 'chapters')}")


async def test_engine_chapters_are_archived_as_podcasting_2_json_and_advertised(
    container: Container, runner: Runner, engine: FakeEngine, client: httpx.AsyncClient
) -> None:
    feed_id, items = await _archived_channel(container, runner, engine)
    layout = container.layout
    key = layout.chapters_key(feed_id, items[2])

    assert json.loads(layout.store.get_bytes(key) or b"") == PODCAST_CHAPTERS
    async with uow(container) as unit:
        (row,) = [a for a in await unit.assets.for_item(items[2]) if a.kind == AssetKind.chapters]
        assert (row.state, row.mime, row.format) == (
            AssetState.archived,
            CHAPTERS_MIME,
            AssetFormat.json,
        )
        assert row.local_path == f"assets/{items[2]}.chapters.json"
        stored = layout.store.head(key)
        assert stored is not None and row.size_bytes == stored.size
        # No chapters from the Engine, no file: an empty document is never written.
        assert not [a for a in await unit.assets.for_item(items[1]) if a.kind == AssetKind.chapters]
    assert not layout.store.exists(layout.chapters_key(feed_id, items[1]))

    # The Mirror Feed links the file under the type it now really is, and serves it.
    (link,) = await _advertised(client, feed_id)
    assert link.get("type") == CHAPTERS_MIME
    assert link.get("url").endswith(f"/feeds/{feed_id}/assets/{items[2]}.chapters.json")
    served = await client.get(link.get("url"))
    assert served.status_code == 200
    assert served.headers["content-type"].startswith(CHAPTERS_MIME)
    assert served.json() == PODCAST_CHAPTERS


async def test_worker_startup_upgrades_chapters_archived_raw(
    container: Container, runner: Runner, engine: FakeEngine, client: httpx.AsyncClient
) -> None:
    feed_id, items = await _archived_channel(container, runner, engine)
    layout = container.layout
    key = layout.chapters_key(feed_id, items[2])
    layout.write_json(key, RAW_CHAPTERS[:2])  # what archives before 1.4 left behind

    with structlog.testing.capture_logs() as events:
        await worker_main._upgrade_chapters(layout)
        await worker_main._upgrade_chapters(layout)  # idempotent

    upgraded = [e for e in events if e["event"] == "worker.chapters_upgraded"]
    assert [(e["scanned"], e["upgraded"], e["unreadable"]) for e in upgraded] == [
        (1, 1, 0),
        (1, 0, 0),
    ]
    assert json.loads(layout.store.get_bytes(key) or b"") == PODCAST_CHAPTERS
    (link,) = await _advertised(client, feed_id)
    assert (await client.get(link.get("url"))).json() == PODCAST_CHAPTERS


async def test_a_failed_upgrade_never_stops_the_worker(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_layout: object) -> None:
        raise PermissionError("feeds/ is not readable")

    monkeypatch.setattr(worker_main, "upgrade_chapter_files", boom)
    with structlog.testing.capture_logs() as events:
        await worker_main._upgrade_chapters(container.layout)
    assert [(e["event"], e["log_level"]) for e in events] == [
        ("worker.chapters_upgrade_failed", "error")
    ]
