"""Quotas: what a User's Feeds use against what the configuration allows them.

Usage is ``SUM(feeds.storage_bytes)`` over the Owner's Feeds; the quota comes from
the :class:`UserDirectory` (removed Users get the default). Enforcement (blocking
archives, refusing uploads and manual archives) arrives with the quota work; the
seams below keep today's behaviour until then.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Final

from copycast.application.models import QuotaStatus
from copycast.application.services.context import UnitOfWorkPort
from copycast.application.users import UserDirectory
from copycast.domain.quota import quota_state

RELEASE_PER_TICK: Final = 20
"""Blocked items one scheduler tick may release per Owner."""


async def quota_status(uow: UnitOfWorkPort, users: UserDirectory, username: str) -> QuotaStatus:
    """``username``'s usage, quota and state; ``removed`` when no longer configured."""
    used = await uow.quota.owned_storage_bytes(username)
    quota = users.quota_bytes(username)
    return QuotaStatus(
        username=username,
        used_bytes=used,
        quota_bytes=quota,
        state=quota_state(used, quota),
        removed=not users.is_configured(username),
    )


async def quota_statuses(uow: UnitOfWorkPort, users: UserDirectory) -> list[QuotaStatus]:
    """Every configured User plus removed Owners that still own Feeds."""
    usage = await uow.quota.storage_by_owner()
    names = list(dict.fromkeys([*users.usernames(), *sorted(usage)]))
    return [await quota_status(uow, users, name) for name in names]


async def require_room(
    uow: UnitOfWorkPort, users: UserDirectory, owner: str, *, adding_bytes: int = 0
) -> QuotaStatus:
    """The Owner's status; will raise ``OverQuota`` when it is over or ``adding_bytes`` does not
    fit. Not enforced yet: always returns the status."""
    return await quota_status(uow, users, owner)


async def remaining_bytes(uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> int | None:
    """Bytes the Owner may still add; None (unlimited) until quotas are enforced."""
    return None


async def release_if_room(uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> int:
    """Re-queue the Owner's blocked items that fit; nothing is blocked yet, so 0."""
    return 0


async def gate_manual(uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> None:
    """A manual archive or retry: will raise ``OverQuota`` when the Owner is over. No-op."""


async def on_manual_wanted(uow: UnitOfWorkPort, item_ids: Collection[str]) -> None:
    """Right after a manual ``set_wanted``: will clear the items' block. No-op."""


__all__ = [
    "RELEASE_PER_TICK",
    "gate_manual",
    "on_manual_wanted",
    "quota_status",
    "quota_statuses",
    "release_if_room",
    "remaining_bytes",
    "require_room",
]
