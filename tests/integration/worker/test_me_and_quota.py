"""``get_me`` and ``quota_status`` over a real database, as the open-mode Admin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from copycast.adapters.db.uow import uow_of
from copycast.app import Container
from copycast.application.services import auth, quota
from copycast.domain.enums import AuthMode, PrincipalVia
from copycast.domain.exceptions import Unauthorized
from copycast.domain.users import Principal

pytestmark = pytest.mark.integration


@dataclass
class _As:
    """The container's services seen by another Principal (the foundation's ScopedContext)."""

    base: Container
    principal: Principal

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)


async def test_get_me_answers_for_the_principal(container: Container) -> None:
    bucket = await container.services.ensure_default_bucket()
    async with uow_of(container.uow_factory()) as uow:
        feed = await uow.feeds.require(bucket.id)
        feed.storage_bytes = 1234
    me = await auth.get_me(_As(container, Principal.open_admin("copycast")))  # type: ignore[arg-type]
    assert me.username == "copycast" and me.admin and me.totp is False
    assert me.auth_mode is AuthMode.open and me.via is PrincipalVia.open
    assert me.quota.used_bytes == 1234 and me.quota.quota_bytes is None
    assert me.quota.state == "ok" and me.quota.removed is False


async def test_get_me_refuses_system(container: Container) -> None:
    with pytest.raises(Unauthorized):
        await container.services.get_me()


async def test_quota_statuses_list_removed_owners_too(container: Container) -> None:
    await container.services.ensure_default_bucket()
    await container.services.ensure_default_bucket("gone")
    async with uow_of(container.uow_factory()) as uow:
        statuses = await quota.quota_statuses(uow, container.users)
        assert [(s.username, s.removed) for s in statuses] == [
            ("copycast", False),
            ("gone", True),
        ]
        assert (await quota.require_room(uow, container.users, "copycast")).state == "ok"
        assert await quota.remaining_bytes(uow, container.users, "copycast") is None
        assert await quota.release_if_room(uow, container.users, "copycast") == 0
        await quota.gate_manual(uow, container.users, "copycast")
        await quota.on_manual_wanted(uow, ["x"])
