"""Timing on the 2000-item fixture (limits relaxed 5x under CI)."""

from __future__ import annotations

import os
import time

import httpx
import pytest
from sqlalchemy import text

from copycast.app import Container
from tests.integration.api.conftest import create_mirror
from tests.support.origin import Origin
from tests.support.paths import FEED_URL, api
from tests.support.xml import parse

pytestmark = pytest.mark.integration

RELAX = 5.0 if os.environ.get("CI") else 1.0
CREATE_SECONDS = 5.0 * RELAX
RENDER_SECONDS = 3.0 * RELAX
CACHED_SECONDS = 0.5 * RELAX
PAGE_SECONDS = 1.0 * RELAX


async def test_two_thousand_items(
    client: httpx.AsyncClient, origin: Origin, container: Container
) -> None:
    url = origin.url_for("/rss/large_2000.xml")
    started = time.perf_counter()
    mirror = await create_mirror(client, url, selection="1")
    assert time.perf_counter() - started < CREATE_SECONDS
    assert mirror["counts"]["available"] + mirror["counts"]["wanted"] == 2000

    # Mark everything archived straight in the database (the FakeEngine would take a while).
    async with container.uow_factory() as uow:
        await uow.session.execute(
            text(
                "UPDATE catalog_items SET archive_state = 'archived', "
                "media_path = 'media/' || id || '.mp3', media_mime = 'audio/mpeg', "
                "media_bytes = 4407, archived_at = now(), wanted_reason = NULL "
                "WHERE feed_id = :feed_id"
            ),
            {"feed_id": mirror["id"]},
        )
        await uow.feeds.bump_revision(mirror["id"])

    started = time.perf_counter()
    response = await client.get(FEED_URL(mirror["id"]))
    first = time.perf_counter() - started
    assert response.status_code == 200
    assert first < RENDER_SECONDS, first
    assert len(parse(response.content).find("channel").findall("item")) == 2000

    started = time.perf_counter()
    cached = await client.get(FEED_URL(mirror["id"]))
    assert cached.status_code == 200 and cached.headers["etag"] == response.headers["etag"]
    assert time.perf_counter() - started < CACHED_SECONDS

    started = time.perf_counter()
    page = await client.get(
        api(f"/feeds/{mirror['id']}/items"), params={"limit": 100, "offset": 1900}
    )
    assert page.status_code == 200
    assert time.perf_counter() - started < PAGE_SECONDS
    body = page.json()
    assert body["total"] == 2000 and len(body["items"]) == 100
