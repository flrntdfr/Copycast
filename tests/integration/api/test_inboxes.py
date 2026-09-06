"""Inboxes through the API: the default Inbox, Requests and their expansion, synchronous prune."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from copycast.adapters.api.app import create_app
from copycast.app import Container
from copycast.settings import Settings
from copycast.worker.runner import Runner
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.paths import MEDIA_URL, api

pytestmark = pytest.mark.integration


async def default_inbox(client: httpx.AsyncClient) -> dict[str, Any]:
    feeds = (await client.get(api("/inboxes"))).json()["feeds"]
    inbox: dict[str, Any] = next(f for f in feeds if f["name"] == "Copycast")
    return inbox


async def test_default_inbox_is_idempotent_across_lifespans(
    settings: Settings, container: Container, client: httpx.AsyncClient
) -> None:
    first = await default_inbox(client)
    second_app = create_app(settings, container)
    async with second_app.router.lifespan_context(second_app):
        transport = httpx.ASGITransport(app=second_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            feeds = (await http.get(api("/inboxes"))).json()["feeds"]
    assert [f["id"] for f in feeds] == [first["id"]]


async def test_create_update_inbox(client: httpx.AsyncClient) -> None:
    created = await client.post(api("/inboxes"), json={"name": "  Later  ", "autoprune_days": 7})
    assert created.status_code == 201, created.text
    inbox = created.json()
    assert inbox["kind"] == "inbox" and inbox["name"] == "Later" and inbox["autoprune_days"] == 7
    assert created.headers["location"].endswith(api(f"/feeds/{inbox['id']}"))
    assert inbox["feed_url"].endswith(f"/feeds/{inbox['id']}.xml")

    renamed = await client.patch(api(f"/inboxes/{inbox['id']}"), json={"name": "Sooner"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Sooner"
    off = await client.patch(api(f"/inboxes/{inbox['id']}"), json={"autoprune_days": None})
    assert off.status_code == 200 and off.json()["autoprune_days"] is None
    invalid = await client.patch(api(f"/inboxes/{inbox['id']}"), json={"autoprune_days": 0})
    assert invalid.status_code == 422
    by_name = await client.patch(api("/inboxes/sooner"), json={"name": "Sooner"})
    assert by_name.status_code == 200 and by_name.json()["id"] == inbox["id"]
    unknown = await client.patch(api("/inboxes/none-such"), json={"name": "x"})
    assert unknown.status_code == 404
    both = (await client.get(api("/inboxes"))).json()["feeds"]
    assert {f["name"] for f in both} == {"Copycast", "Sooner"}


async def test_request_expansion_and_prune(
    client: httpx.AsyncClient, runner: Runner, engine: FakeEngine
) -> None:
    inbox = await default_inbox(client)
    url = "https://www.youtube.com/playlist?list=PLapi"
    engine.script_listing(url, listing(2, service="YouTube", title="Playlist"))

    queued = await client.post(api(f"/inboxes/{inbox['id']}/requests"), json={"url": url})
    assert queued.status_code == 202, queued.text
    request = queued.json()
    assert request["status"] == "queued" and request["requested_via"] == "ui"
    assert request["inbox_id"] == inbox["id"] and request["url"] == url
    assert request["job"] and request["job"]["kind"] == "expand_request"

    await runner.run_until_idle()
    expanded = await client.get(api(f"/inboxes/{inbox['id']}/requests/{request['id']}"))
    assert expanded.status_code == 200
    body = expanded.json()
    assert body["status"] == "expanded" and body["item_count"] == 2
    assert len(body["items"]) == 2 and all(i["state"] == "archived" for i in body["items"])
    assert all(request["id"] in i["request_ids"] for i in body["items"])
    page = await client.get(api(f"/inboxes/{inbox['id']}/requests"))
    assert page.status_code == 200 and page.json()["total"] == 1
    assert page.json()["requests"][0]["id"] == request["id"]
    refreshed = (await client.get(api(f"/feeds/{inbox['id']}"))).json()
    assert refreshed["request_count"] == 1 and refreshed["episode_count"] == 2

    # Download one item, then prune "downloaded at least once": only it goes.
    first, second = body["items"]
    downloaded = await client.get(MEDIA_URL(inbox["id"], first["id"], first["media"]["ext"]))
    assert downloaded.status_code == 200
    preview = await client.post(
        api(f"/inboxes/{inbox['id']}/prune"), json={"downloaded": True, "dry_run": True}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "matched": 1,
        "deleted_count": 0,
        "bytes_freed": first["media"]["bytes"],
        "dry_run": True,
    }
    pruned = await client.post(api(f"/inboxes/{inbox['id']}/prune"), json={"downloaded": True})
    assert pruned.status_code == 200
    assert pruned.json()["deleted_count"] == 1 and pruned.json()["dry_run"] is False
    remaining = (
        await client.get(api(f"/feeds/{inbox['id']}/items"), params={"listed": "true"})
    ).json()["items"]
    assert [i["id"] for i in remaining] == [second["id"]]
    hidden = (
        await client.get(api(f"/feeds/{inbox['id']}/items"), params={"listed": "false"})
    ).json()["items"]
    assert [i["id"] for i in hidden] == [first["id"]] and hidden[0]["state"] == "deleted"

    no_criterion = await client.post(api(f"/inboxes/{inbox['id']}/prune"), json={})
    assert no_criterion.status_code == 422
    by_name = await client.post(api("/inboxes/copycast/prune"), json={"older_than_days": 0})
    assert by_name.status_code == 200 and by_name.json()["deleted_count"] == 1


async def test_request_validation(client: httpx.AsyncClient) -> None:
    inbox = await default_inbox(client)
    blank = await client.post(api(f"/inboxes/{inbox['id']}/requests"), json={"url": "   "})
    assert blank.status_code == 422
    missing = await client.post(api("/inboxes/none-such/requests"), json={"url": "http://x"})
    assert missing.status_code == 404
