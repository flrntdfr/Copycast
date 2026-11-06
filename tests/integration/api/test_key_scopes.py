"""Argument-level key scopes on ``/api``: the services refuse what deletes on a schedule.

A ``write`` key may create and change Mirrors and Local feeds, but a Rolling window, an
expiring Ephemeral policy, ``engine_options`` and ``autoprune_days`` need ``full``, on
``/api`` exactly as over MCP (``application.services.keyscope``). Sessions are never
held to a key scope.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.support.origin import Origin
from tests.support.paths import api
from tests.support.users import mint_key, session_headers

pytestmark = pytest.mark.integration

FEED_PATH = "rss/itunes_podcast20.xml"


def problem(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


async def keys_of_alice(client: httpx.AsyncClient) -> tuple[dict[str, str], dict[str, str]]:
    """Alice's session headers, and her ``write`` and ``full`` keys as Bearer headers."""
    session = await session_headers(client, "alice")
    client.headers.update(session)
    write = await mint_key(client, "write", "write")
    full = await mint_key(client, "full", "full")
    client.headers.pop("Cookie")
    client.cookies.clear()
    return session, {"write": f"Bearer {write}", "full": f"Bearer {full}"}


def refused(response: httpx.Response, what: str) -> None:
    assert response.status_code == 403, response.text
    body = problem(response)
    assert body["type"] == "urn:copycast:problem:forbidden"
    assert body["detail"] == (
        f"{what} needs an API key with scope 'full'; this key has scope 'write'"
    )


async def test_scheduled_deletions_and_engine_options_need_a_full_key(
    users_client: httpx.AsyncClient, origin: Origin
) -> None:
    session, keys = await keys_of_alice(users_client)
    write = {"Authorization": keys["write"]}
    full = {"Authorization": keys["full"]}
    url = origin.url_for(FEED_PATH)

    rolling = {"source_url": url, "backfill": {"mode": "rolling", "latest_n": 2}}
    refused(
        await users_client.post(api("/mirrors"), json=rolling, headers=write),
        "create_mirror (backfill.mode rolling)",
    )
    expiring = {"source_url": url, "backfill": {"mode": "automatic", "retention_days": 3}}
    refused(
        await users_client.post(api("/mirrors"), json=expiring, headers=write),
        "create_mirror (backfill.mode automatic)",
    )
    tuned = {"source_url": url, "backfill": {"mode": "new"}, "engine_options": {"ratelimit": 5}}
    refused(
        await users_client.post(api("/mirrors"), json=tuned, headers=write),
        "create_mirror (engine_options)",
    )
    # Nothing was created by the refused calls; a plain Mirror is write-key business.
    created = await users_client.post(
        api("/mirrors"), json={"source_url": url, "backfill": {"mode": "new"}}, headers=write
    )
    assert created.status_code == 201, created.text
    mirror_id = created.json()["id"]

    patch_path = api(f"/mirrors/{mirror_id}")
    refused(
        await users_client.patch(
            patch_path, json={"backfill": {"mode": "rolling", "latest_n": 2}}, headers=write
        ),
        "update_mirror (backfill.mode rolling)",
    )
    refused(
        await users_client.patch(patch_path, json={"engine_options": {}}, headers=write),
        "update_mirror (engine_options)",
    )
    renamed = await users_client.patch(patch_path, json={"title": "Mine"}, headers=write)
    assert renamed.status_code == 200 and renamed.json()["title"] == "Mine"
    tuned_ok = await users_client.patch(
        patch_path, json={"engine_options": {"ratelimit": 5}}, headers=full
    )
    assert tuned_ok.status_code == 200 and tuned_ok.json()["engine_options"] == {"ratelimit": 5}
    rolled = await users_client.patch(
        patch_path, json={"backfill": {"mode": "rolling", "latest_n": 2}}, headers=full
    )
    assert rolled.status_code == 200 and rolled.json()["backfill"]["mode"] == "rolling"

    refused(
        await users_client.post(
            api("/buckets"), json={"name": "Pruned", "autoprune_days": 7}, headers=write
        ),
        "create_bucket (autoprune_days)",
    )
    bucket = await users_client.post(api("/buckets"), json={"name": "Later"}, headers=write)
    assert bucket.status_code == 201
    bucket_path = api(f"/buckets/{bucket.json()['id']}")
    refused(
        await users_client.patch(bucket_path, json={"autoprune_days": 7}, headers=write),
        "update_bucket (autoprune_days)",
    )
    # Switching Retention off deletes nothing: a write key may.
    off = await users_client.patch(bucket_path, json={"autoprune_days": None}, headers=write)
    assert off.status_code == 200
    on = await users_client.patch(bucket_path, json={"autoprune_days": 7}, headers=full)
    assert on.status_code == 200 and on.json()["autoprune_days"] == 7

    # A session is held to no key scope.
    by_session = await users_client.post(
        api("/buckets"), json={"name": "Session pruned", "autoprune_days": 3}, headers=session
    )
    assert by_session.status_code == 201, by_session.text


async def test_session_only_routes_refuse_every_key(users_client: httpx.AsyncClient) -> None:
    _, keys = await keys_of_alice(users_client)
    response = await users_client.get(api("/me"), headers={"Authorization": keys["full"]})
    assert response.status_code == 403
    assert problem(response)["type"] == "urn:copycast:problem:forbidden"
