"""The Lock (``archive_flag``): an Archive Mirror locks what it archives, ``delete_item``
refuses a locked Episode, purging the bucket spares it, and switching to the Archive policy
locks what is archived."""

from __future__ import annotations

import httpx
import pytest

from copycast.worker.runner import Runner
from tests.integration.api.conftest import Source, archived_items, create_mirror, items_of
from tests.support.paths import api

pytestmark = pytest.mark.integration


async def set_lock(client: httpx.AsyncClient, feed_id: str, item_id: str, *, locked: bool) -> None:
    response = await client.put(
        api(f"/feeds/{feed_id}/items/{item_id}/archive-flag"), json={"archive_flag": locked}
    )
    assert response.status_code == 200, response.text
    assert response.json()["archive_flag"] is locked


async def test_an_archive_mirror_locks_and_a_locked_episode_is_not_deleted(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("locked", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="all")
    items = await archived_items(client, runner, mirror["id"])
    assert len(items) == 2 and all(item["archive_flag"] for item in items)
    first, second = items

    refused = await client.delete(api(f"/feeds/{mirror['id']}/items/{first['id']}"))
    assert refused.status_code == 409
    assert refused.headers["content-type"].startswith("application/problem+json")
    assert "locked" in refused.json()["detail"]
    assert {i["id"] for i in await items_of(client, mirror["id"], state="archived")} == {
        first["id"],
        second["id"],
    }

    # Unlocked by hand, it can be deleted, and purging the bucket takes it too.
    await set_lock(client, mirror["id"], first["id"], locked=False)
    listing = (await client.get(api(f"/feeds/{mirror['id']}/objects"))).json()
    locks = {obj["item_id"]: obj["locked"] for obj in listing["objects"] if obj["kind"] == "media"}
    assert locks == {first["id"]: False, second["id"]: True}
    preview = await client.post(api(f"/feeds/{mirror['id']}/clear"), json={})
    assert preview.status_code == 200, preview.text
    counts = preview.json()
    assert (counts["matched"], counts["kept_count"], counts["dry_run"]) == (1, 1, True)
    cleared = await client.post(api(f"/feeds/{mirror['id']}/clear"), json={"dry_run": False})
    assert (cleared.json()["deleted_count"], cleared.json()["kept_count"]) == (1, 1)
    archived = await items_of(client, mirror["id"], state="archived")
    assert [i["id"] for i in archived] == [second["id"]]

    await set_lock(client, mirror["id"], second["id"], locked=False)
    deleted = await client.delete(api(f"/feeds/{mirror['id']}/items/{second['id']}"))
    assert deleted.status_code == 204


async def test_switching_to_the_archive_policy_locks_what_is_archived(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("switched", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="automatic")
    await runner.run_until_idle()
    available = await items_of(client, mirror["id"])
    archive = await client.post(api(f"/feeds/{mirror['id']}/archive-available"))
    assert archive.status_code == 202
    items = await archived_items(client, runner, mirror["id"])
    assert len(items) == len(available) and not any(i["archive_flag"] for i in items)

    await switch(client, mirror["id"], "new")
    assert all(i["archive_flag"] for i in await items_of(client, mirror["id"]))

    # A hand-made unlock survives saving the policy again and switching away (which unlocks
    # nothing); switching back to the Archive policy locks again.
    await set_lock(client, mirror["id"], items[0]["id"], locked=False)
    expected = {items[0]["id"]: False, items[1]["id"]: True}
    for mode in ("new", "automatic"):
        await switch(client, mirror["id"], mode)
        locks = {i["id"]: i["archive_flag"] for i in await items_of(client, mirror["id"])}
        assert locks == expected, mode
    await switch(client, mirror["id"], "all")
    assert all(i["archive_flag"] for i in await items_of(client, mirror["id"]))


async def test_a_mirror_in_sync_with_its_source_is_never_locked_automatically(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    """Sync skips locked Episodes, so the Archive policy leaves a synced Mirror unlocked;
    turning Sync off then locks what it holds, as a switch to the Archive policy would."""
    url = source.write_rss("synced", items=[2, 1], artwork=False)
    mirror = await create_mirror(client, url, mode="all", sync_deletions=True)
    items = await archived_items(client, runner, mirror["id"])
    assert len(items) == 2 and not any(item["archive_flag"] for item in items)

    response = await client.patch(api(f"/mirrors/{mirror['id']}"), json={"sync_deletions": False})
    assert response.status_code == 200, response.text
    assert all(i["archive_flag"] for i in await items_of(client, mirror["id"], state="archived"))


async def switch(client: httpx.AsyncClient, feed_id: str, mode: str) -> None:
    response = await client.patch(api(f"/mirrors/{feed_id}"), json={"backfill": {"mode": mode}})
    assert response.status_code == 200, response.text
