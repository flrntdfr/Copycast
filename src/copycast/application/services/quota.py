"""Quotas: what a User's Feeds use against what the configuration allows them (ADR 0029).

Usage is ``SUM(feeds.storage_bytes)`` over the Owner's Feeds; the quota comes from
the :class:`UserDirectory` (removed Users get the default). At quota, new downloads
are refused and nothing is deleted on the Owner's behalf (D2):

- the manual entry points (``archive_item``, ``archive_available``, ``retry_failed``,
  ``select_items``) call :func:`gate_manual` before anything is set wanted, and
  :func:`on_manual_wanted` right after, which lifts the block of what was asked for;
- the archive job blocks an item instead of fetching it when the Owner is over, or when
  the Engine's estimate does not fit :func:`remaining_bytes` (``blocked_reason``);
- uploads check :func:`require_room` with their declared and received sizes;
- the scheduler (and the prune job) re-queue blocked items through
  :func:`release_if_room`, a bounded, budgeted walk, so a near-quota Owner never gets a
  job storm.

Backfill, Follow, policy switches and Light Refresh queue as usual: the archive job
blocks what does not fit.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Final

from copycast.application.events import ItemEvent
from copycast.application.models import QuotaStatus
from copycast.application.services.context import ServiceContext, UnitOfWorkPort
from copycast.application.users import UserDirectory
from copycast.domain.enums import ArchiveState, JobTrigger, WantedReason
from copycast.domain.exceptions import OverQuota
from copycast.domain.quota import format_size, quota_state, remaining
from copycast.logging import get_logger

log = get_logger(__name__)

RELEASE_PER_TICK: Final = 20
"""Blocked items one scheduler tick may release per Owner."""

BLOCKED_DETAIL: Final = "the episode does not fit in the storage left; free some space"


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


def over_quota(status: QuotaStatus, message: str | None = None) -> OverQuota:
    """The 507 problem for ``status`` (``used_bytes`` and ``quota_bytes`` as extensions)."""
    quota = status.quota_bytes if status.quota_bytes is not None else status.used_bytes
    if message is None:
        message = (
            f"over quota: {format_size(status.used_bytes)} of {format_size(quota)} used; "
            "free some space or ask for a larger quota"
        )
    return OverQuota(used_bytes=status.used_bytes, quota_bytes=quota, message=message)


async def require_room(
    uow: UnitOfWorkPort, users: UserDirectory, owner: str, *, adding_bytes: int = 0
) -> QuotaStatus:
    """The Owner's status; ``OverQuota`` when it is over, or when ``adding_bytes`` more
    would not fit."""
    status = await quota_status(uow, users, owner)
    if status.state == "over":
        raise over_quota(status)
    if status.quota_bytes is not None and status.used_bytes + adding_bytes > status.quota_bytes:
        left = format_size(max(0, status.quota_bytes - status.used_bytes))
        raise over_quota(
            status,
            f"over quota: {format_size(adding_bytes)} do not fit in the {left} left; "
            "free some space or ask for a larger quota",
        )
    return status


async def remaining_bytes(uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> int | None:
    """Bytes the Owner may still add (never negative); None means unlimited."""
    status = await quota_status(uow, users, owner)
    return remaining(status.used_bytes, status.quota_bytes)


def wanted_priority(reason: str | None) -> int:
    """The archive priority ``wanted_reason`` gives: manual and request 50, follow 100,
    backfill 200 (as ``items`` and the worker's ``common.wanted_priority`` compute it)."""
    from copycast.application.services.items import (  # items imports this module
        PRIORITY_BACKFILL,
        PRIORITY_FOLLOW,
        PRIORITY_MANUAL,
    )

    if reason == WantedReason.backfill:
        return PRIORITY_BACKFILL
    if reason == WantedReason.follow:
        return PRIORITY_FOLLOW
    return PRIORITY_MANUAL


async def release_if_room(uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> int:
    """Re-queue the Owner's blocked items that fit; returns how many were released.

    Nothing while the Owner is over, and nothing for a removed User (their Feeds are inert
    until they are configured again or reassigned, §3.3). Otherwise at most
    :data:`RELEASE_PER_TICK` items, in archive priority, among those that could fit at
    all (the repository leaves out estimates above the room left): one blocked with an
    unknown size (the Owner was over then) is released; one whose estimate fits the
    budget left is released and spends it; one that no longer fits is skipped, so a
    single large Episode never holds back the smaller ones behind it. An item too large
    for what is left costs no job and no extraction; it is released once there is room.
    """
    from copycast.application.services.items import (  # items imports this module
        enqueue_archive,
    )

    if not users.is_configured(owner):
        return 0
    status = await quota_status(uow, users, owner)
    if status.state == "over":
        return 0
    budget = remaining(status.used_bytes, status.quota_bytes)
    rows = await uow.quota.blocked_for_release(owner, limit=RELEASE_PER_TICK, max_bytes=budget)
    released: list[tuple[str, str, str | None]] = []
    for row in rows:
        size = row.blocked_bytes
        if size is not None and budget is not None:
            if size > budget:
                continue
            budget -= size
        released.append((row.id, row.feed_id, row.wanted_reason))
    if not released:
        return 0
    await uow.quota.unblock([item_id for item_id, _, _ in released])
    items = {item.id: item for item in await uow.catalog.get_many(i for i, _, _ in released)}
    for item_id, feed_id, reason in released:
        await uow.publish(ItemEvent(feed_id=feed_id, item_id=item_id, state=ArchiveState.wanted))
        item = items.get(item_id)
        if item is not None:
            await enqueue_archive(
                uow, item, trigger=JobTrigger.policy, priority=wanted_priority(reason)
            )
    await uow.notify_jobs()
    log.info("quota.released", owner=owner, count=len(released))
    return len(released)


async def gate_manual(uow: UnitOfWorkPort, users: UserDirectory, owner: str) -> None:
    """A manual archive or retry (§3.5): ``OverQuota`` (507) when the Owner is over."""
    status = await quota_status(uow, users, owner)
    if status.state == "over":
        raise over_quota(status)


async def on_manual_wanted(uow: UnitOfWorkPort, item_ids: Collection[str]) -> None:
    """Right after a manual ``set_wanted``: lift the block of the items asked for, so they
    are queued again (the archive job blocks them anew when they still do not fit)."""
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        return
    blocked = [item for item in await uow.catalog.get_many(ids) if item.blocked_reason]
    if not blocked:
        return
    await uow.quota.unblock([item.id for item in blocked])
    for item in blocked:
        await uow.publish(
            ItemEvent(feed_id=item.feed_id, item_id=item.id, state=ArchiveState.wanted)
        )


async def feed_owner_over_quota(ctx: ServiceContext, feed_id: str) -> OverQuota:
    """The 507 problem for the Owner of ``feed_id``, for an item the archive job blocked
    (``/feeds`` first plays, which run as SYSTEM)."""
    async with ctx.uow_factory() as uow:
        feed = await uow.feeds.require(feed_id)
        status = await quota_status(uow, ctx.users, feed.owner)
    if status.state == "over":
        return over_quota(status)
    return over_quota(status, BLOCKED_DETAIL)


__all__ = [
    "BLOCKED_DETAIL",
    "RELEASE_PER_TICK",
    "feed_owner_over_quota",
    "gate_manual",
    "on_manual_wanted",
    "over_quota",
    "quota_status",
    "quota_statuses",
    "release_if_room",
    "remaining_bytes",
    "require_room",
    "wanted_priority",
]
