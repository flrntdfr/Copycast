"""Uploads and quotas, and who an upload belongs to (spec §3.5, §3.8).

``create_upload`` refuses a declared size that does not fit (507 ``over-quota``);
the ``PUT`` stops at the room left and keeps nothing; ``add_uploads`` and
``create_serial_bucket`` check the whole batch before copying anything. An upload
belongs to the User who staged it: another User's is 404, and an Admin may use it only
in a Feed of its Owner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from copycast.app import Container
from tests.integration.worker.test_quota_blocking import set_quota, use_bytes
from tests.support.audio import tagged_mp3
from tests.support.paths import api
from tests.support.world import World

pytestmark = pytest.mark.integration

OVER_QUOTA = "urn:copycast:problem:over-quota"


async def stage(client: httpx.AsyncClient, name: str, **body: Any) -> dict[str, Any]:
    staged = await client.post(api("/uploads"), json={"filename": name, **body})
    assert staged.status_code == 201, staged.text
    payload: dict[str, Any] = staged.json()
    return payload


async def put(client: httpx.AsyncClient, staged: dict[str, Any], body: bytes) -> httpx.Response:
    return await client.put(urlsplit(staged["upload_url"]).path, content=body)


async def received(client: httpx.AsyncClient, name: str, body: bytes) -> dict[str, Any]:
    staged = await stage(client, name)
    response = await put(client, staged, body)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def over_quota(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 507, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "retry-after" not in response.headers
    problem: dict[str, Any] = response.json()
    assert problem["type"] == OVER_QUOTA
    return problem


async def test_a_declared_size_that_does_not_fit_is_refused(
    client: httpx.AsyncClient, container: Container
) -> None:
    set_quota(container, 10_000)
    await use_bytes(container, 4_000)
    problem = over_quota(
        await client.post(api("/uploads"), json={"filename": "big.mp3", "size_bytes": 6_001})
    )
    assert (problem["used_bytes"], problem["quota_bytes"]) == (4_000, 10_000)
    fits = await stage(client, "fits.mp3", size_bytes=6_000)
    assert fits["owner"] == "copycast" and fits["state"] == "pending"
    layout = container.layout
    meta = layout.read_json(f"{layout.uploads_prefix()}{fits['id']}/upload.json")
    assert (meta["owner"], meta["declared_bytes"]) == ("copycast", 6_000)

    await use_bytes(container, 6_000)  # now exactly at the quota: over, whatever the size
    over_quota(await client.post(api("/uploads"), json={"filename": "any.mp3"}))


async def test_the_put_stops_at_the_room_left_and_keeps_nothing(
    client: httpx.AsyncClient, container: Container, tmp_path: Path
) -> None:
    body = tagged_mp3(tmp_path, "a.mp3", title="A")
    set_quota(container, len(body) - 1)
    staged = await stage(client, "a.mp3")
    problem = over_quota(await put(client, staged, body))
    assert problem["quota_bytes"] == len(body) - 1
    again = (await client.get(api(f"/uploads/{staged['id']}"))).json()
    assert again["state"] == "pending" and again["size_bytes"] is None
    prefix = f"{container.layout.uploads_prefix()}{staged['id']}/"
    assert [o.key for o in container.layout.store.list(prefix)] == [prefix + "upload.json"]

    set_quota(container, len(body))
    response = await put(client, staged, body)
    assert response.status_code == 200, response.text
    assert response.json()["size_bytes"] == len(body)


async def test_a_batch_that_does_not_fit_copies_nothing(
    client: httpx.AsyncClient, container: Container, tmp_path: Path
) -> None:
    names = ["01.mp3", "02.mp3", "03.mp3"]
    uploads = [await received(client, n, tagged_mp3(tmp_path, n, title=n)) for n in names]
    sizes = [u["size_bytes"] for u in uploads]
    # Each one alone fits; the three together do not.
    set_quota(container, sum(sizes) - 1)
    assert all(size <= sum(sizes) - 1 for size in sizes)
    bucket_id = (await container.services.ensure_default_bucket()).id
    feeds_before = {f["id"] for f in (await client.get(api("/feeds"))).json()["feeds"]}

    ids = [u["id"] for u in uploads]
    over_quota(await client.post(api(f"/buckets/{bucket_id}/uploads"), json={"upload_ids": ids}))
    episodes = [{"upload_id": i, "title": n} for i, n in zip(ids, names, strict=True)]
    over_quota(
        await client.post(api("/serial-buckets"), json={"name": "Voyage", "episodes": episodes})
    )

    items = (await client.get(api(f"/feeds/{bucket_id}/items"))).json()
    assert items["total"] == 0
    feeds_after = {f["id"] for f in (await client.get(api("/feeds"))).json()["feeds"]}
    assert feeds_after == feeds_before
    for upload_id in ids:
        assert (await client.get(api(f"/uploads/{upload_id}"))).json()["state"] == "received"

    set_quota(container, sum(sizes))
    added = await client.post(api(f"/buckets/{bucket_id}/uploads"), json={"upload_ids": ids})
    assert added.status_code == 200, added.text
    assert (await client.get(api(f"/feeds/{bucket_id}/items"))).json()["total"] == 3


async def test_another_users_upload_is_not_found(
    world: World,
    alice_client: httpx.AsyncClient,
    bob_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    assert (await alice_client.get(api(f"/uploads/{world.upload_id}"))).status_code == 404
    mine = await received(bob_client, "b.mp3", tagged_mp3(tmp_path, "b.mp3", title="B"))
    assert mine["owner"] == "bob"
    assert (await bob_client.get(api(f"/uploads/{mine['id']}"))).status_code == 200
    assert (await alice_client.get(api(f"/uploads/{mine['id']}"))).status_code == 404
    body = {"upload_ids": [mine["id"]]}
    refused = await alice_client.post(api(f"/buckets/{world.defaults['alice']}/uploads"), json=body)
    assert refused.status_code == 404, refused.text
    serial = await alice_client.post(
        api("/serial-buckets"),
        json={"name": "Stolen", "episodes": [{"upload_id": mine["id"], "title": "x"}]},
    )
    assert serial.status_code == 404, serial.text

    # An Admin sees it, but may add it only to a Feed of its Owner.
    seen = await root_client.get(api(f"/uploads/{mine['id']}"))
    assert seen.status_code == 200 and seen.json()["owner"] == "bob"
    into_own = await root_client.post(api(f"/buckets/{world.defaults['root']}/uploads"), json=body)
    assert into_own.status_code == 404, into_own.text
    into_bobs = await root_client.post(api(f"/buckets/{world.bucket_id}/uploads"), json=body)
    assert into_bobs.status_code == 200, into_bobs.text
