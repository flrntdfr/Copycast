"""Timing on the 2000-item fixture (limits relaxed 5x under CI), and of sign-in failures."""

from __future__ import annotations

import os
import statistics
import time

import httpx
import pytest
import structlog
from sqlalchemy import text

from copycast.app import Container
from copycast.application.passwords import PasswordHasher
from copycast.settings import Settings
from tests.integration.api.conftest import create_mirror, running, settings_with
from tests.support.origin import Origin
from tests.support.paths import FEED_URL, api
from tests.support.users import USERS, user_entry
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


# --------------------------------------------------------------------------- sign-in

TRIES = 4
"""Below the lock threshold of five failures per name."""


async def _median_login_seconds(client: httpx.AsyncClient, names: list[str]) -> float:
    durations: list[float] = []
    for name in names:
        started = time.perf_counter()
        response = await client.post(
            "/auth/login", json={"username": name, "password": "not-the-password"}
        )
        durations.append(time.perf_counter() - started)
        assert response.status_code == 401, response.text
    return statistics.median(durations)


async def test_unknown_user_and_wrong_password_take_the_same_time(
    settings: Settings, db: str
) -> None:
    """Real argon2 parameters: an unknown name runs a dummy verify as costly as a real one.

    Three known names, so the medians cover twelve sign-ins each and stay below the lock
    threshold of every name (four were too few to resist a busy machine).
    """
    real = PasswordHasher()
    known = ("root", "alice", "bob")
    entries = [
        user_entry(USERS[name], password_hash=real.hash(USERS[name].password)) for name in known
    ]
    configured = settings_with(settings, auth={"users": entries})
    async with running(configured) as (_, client):
        await _median_login_seconds(client, ["warm-up"])  # computes the dummy hash once
        wrong = await _median_login_seconds(client, [*known] * TRIES)
        unknown = await _median_login_seconds(
            client, [f"nobody{n}" for n in range(len(known) * TRIES)]
        )
    assert abs(unknown - wrong) <= 0.25 * max(unknown, wrong), (unknown, wrong)


async def test_with_mixed_parameters_an_unknown_name_is_never_faster(
    settings: Settings, db: str
) -> None:
    real = PasswordHasher()
    entries = [
        user_entry(USERS["root"], password_hash=real.hash(USERS["root"].password)),
        user_entry(USERS["alice"]),  # the cheap test parameters
    ]
    configured = settings_with(settings, auth={"users": entries})
    with structlog.testing.capture_logs() as events:
        async with running(configured) as (container, client):
            hasher = container.users.hasher
            assert (hasher.time_cost, hasher.memory_cost) == (real.time_cost, real.memory_cost)
            await _median_login_seconds(client, ["warm-up"])
            unknown = await _median_login_seconds(client, [f"nobody{n}" for n in range(TRIES)])
            slow = await _median_login_seconds(client, ["root"] * TRIES)
            fast = await _median_login_seconds(client, ["alice"] * TRIES)
    assert any("auth.hash_parameters_differ" in str(e["event"]) for e in events)
    assert unknown >= 0.75 * slow, (unknown, slow)
    assert unknown > fast, (unknown, fast)
