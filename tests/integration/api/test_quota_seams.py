"""The quota seams sit where the quota work expects them (``application.services.quota``).

``gate_manual`` runs at the manual entry points only (``archive_item``,
``archive_available``, ``retry_failed``, ``select_items`` unless a dry run), with the
Feed's Owner, before anything is set wanted; ``on_manual_wanted`` runs right after the
items were set wanted. Creating or changing a Mirror never gates. The seams are no-ops
until the quota work fills them, so these tests spy on them, and make the gate refuse
to show that a refusal changes nothing.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import httpx
import pytest

from copycast.application.services import items as items_service
from copycast.application.services import mirrors as mirrors_service
from copycast.application.services.context import UnitOfWorkPort
from copycast.application.users import UserDirectory
from copycast.domain.exceptions import OverQuota
from tests.integration.api.conftest import create_mirror, items_of
from tests.support.origin import Origin
from tests.support.paths import api

pytestmark = pytest.mark.integration

FEED_PATH = "rss/itunes_podcast20.xml"


class Spy:
    def __init__(self) -> None:
        self.gates: list[str] = []
        self.wanted: list[list[str]] = []
        self.refuse = False

    async def gate_manual(self, uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> None:
        self.gates.append(owner)
        if self.refuse:
            raise OverQuota(used_bytes=10, quota_bytes=10)

    async def on_manual_wanted(self, uow: UnitOfWorkPort, item_ids: Collection[str]) -> None:
        self.wanted.append(list(item_ids))


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    seams = Spy()
    for module in (items_service, mirrors_service):
        monkeypatch.setattr(module, "gate_manual", seams.gate_manual)
        monkeypatch.setattr(module, "on_manual_wanted", seams.on_manual_wanted)
    return seams


def problem(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


async def test_creating_and_changing_a_mirror_never_gates(
    client: httpx.AsyncClient, origin: Origin, spy: Spy
) -> None:
    spy.refuse = True
    mirror = await create_mirror(client, origin.url_for(FEED_PATH), selection="1-2")
    assert spy.gates == []
    # The selection Backfill still went through _apply_selection.
    assert len(spy.wanted) == 1 and len(spy.wanted[0]) == 2
    switched = await client.patch(
        api(f"/mirrors/{mirror['id']}"), json={"backfill": {"mode": "selection", "selection": "3"}}
    )
    assert switched.status_code == 200, switched.text
    assert spy.gates == []


async def test_manual_entry_points_gate_with_the_feeds_owner(
    client: httpx.AsyncClient, origin: Origin, spy: Spy
) -> None:
    mirror = await create_mirror(client, origin.url_for(FEED_PATH), mode="new")
    feed_id = mirror["id"]
    available = [item for item in await items_of(client, feed_id) if item["state"] == "available"]
    first = available[0]["id"]

    archived = await client.post(api(f"/feeds/{feed_id}/items/{first}/archive"))
    assert archived.status_code == 202, archived.text
    assert spy.gates == ["copycast"] and spy.wanted == [[first]]

    dry = await client.post(
        api(f"/mirrors/{feed_id}/selections"), json={"selection": "1", "dry_run": True}
    )
    assert dry.status_code == 200 and spy.gates == ["copycast"]
    selected = await client.post(api(f"/mirrors/{feed_id}/selections"), json={"selection": "1"})
    assert selected.status_code == 202, selected.text
    assert spy.gates == ["copycast"] * 2

    for path in ("archive-available", "retry-failed"):
        response = await client.post(api(f"/feeds/{feed_id}/{path}"))
        assert response.status_code in (200, 202), response.text
    assert spy.gates == ["copycast"] * 4


async def test_a_refusing_gate_sets_nothing_wanted(
    client: httpx.AsyncClient, origin: Origin, spy: Spy
) -> None:
    mirror = await create_mirror(client, origin.url_for(FEED_PATH), mode="new")
    feed_id = mirror["id"]
    before = {item["id"]: item["state"] for item in await items_of(client, feed_id)}
    target = next(item_id for item_id, state in before.items() if state == "available")
    spy.refuse = True
    for response in (
        await client.post(api(f"/feeds/{feed_id}/items/{target}/archive")),
        await client.post(api(f"/feeds/{feed_id}/archive-available")),
        await client.post(api(f"/feeds/{feed_id}/retry-failed")),
        await client.post(api(f"/mirrors/{feed_id}/selections"), json={"selection": "1-3"}),
    ):
        assert response.status_code == 507, response.text
        assert problem(response)["type"] == "urn:copycast:problem:over-quota"
    after = {item["id"]: item["state"] for item in await items_of(client, feed_id)}
    assert after == before and spy.wanted == []
