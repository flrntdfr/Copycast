"""The quota service's rules over an in-memory unit of work (spec §1.10, §3.5)."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

import pytest

from copycast.application.events import ItemEvent, JobEvent
from copycast.application.services import quota
from copycast.domain.enums import ArchiveState, WantedReason
from copycast.domain.exceptions import OverQuota


@dataclass
class Row:
    id: str
    feed_id: str = "f1"
    wanted_reason: str | None = WantedReason.backfill.value
    blocked_reason: str | None = "quota"
    blocked_bytes: int | None = None
    archive_state: str = ArchiveState.wanted.value


@dataclass
class FakeQuota:
    used: dict[str, int] = field(default_factory=dict[str, int])
    blocked: list[Row] = field(default_factory=list[Row])
    unblocked: list[list[str]] = field(default_factory=list[list[str]])
    limits: list[int] = field(default_factory=list[int])

    async def owned_storage_bytes(self, owner: str) -> int:
        return self.used.get(owner, 0)

    async def storage_by_owner(self) -> dict[str, int]:
        return dict(self.used)

    async def blocked_for_release(self, owner: str, *, limit: int) -> list[Row]:
        self.limits.append(limit)
        return self.blocked[:limit]

    async def unblock(self, item_ids: Collection[str]) -> None:
        self.unblocked.append(list(item_ids))
        for row in self.blocked:
            if row.id in item_ids:
                row.blocked_reason, row.blocked_bytes = None, None


@dataclass
class FakeCatalog:
    rows: dict[str, Row]

    async def get_many(self, item_ids: Iterable[str]) -> list[Row]:
        return [self.rows[i] for i in item_ids if i in self.rows]


@dataclass
class FakeJob:
    id: str
    priority: int


@dataclass
class FakeJobs:
    enqueued: list[tuple[str | None, int]] = field(default_factory=list[tuple[str | None, int]])

    async def enqueue(self, kind: Any, trigger: Any, **kwargs: Any) -> Any:
        self.enqueued.append((kwargs.get("item_id"), kwargs["priority"]))
        return None  # already queued: enqueue_archive then looks the active job up

    async def active_by_dedup_key(self, key: str) -> Any:
        return None


@dataclass
class FakeUow:
    quota: FakeQuota
    catalog: FakeCatalog
    jobs: FakeJobs = field(default_factory=FakeJobs)
    events: list[Any] = field(default_factory=list[Any])
    notified: int = 0

    async def publish(self, event: Any, **kwargs: Any) -> None:
        self.events.append(event)

    async def notify_jobs(self, payload: str = "") -> None:
        self.notified += 1


@dataclass
class FakeUsers:
    limits: dict[str, int | None]

    def quota_bytes(self, username: str) -> int | None:
        return self.limits.get(username)

    def is_configured(self, username: str) -> bool:
        return username in self.limits

    def usernames(self) -> list[str]:
        return list(self.limits)


def unit(used: int = 0, rows: list[Row] | None = None) -> FakeUow:
    blocked = rows or []
    return FakeUow(
        quota=FakeQuota(used={"alice": used}, blocked=blocked),
        catalog=FakeCatalog(rows={row.id: row for row in blocked}),
    )


USERS = FakeUsers({"alice": 1000, "root": None})


async def test_require_room_refuses_over_and_what_does_not_fit() -> None:
    status = await quota.require_room(unit(500), USERS, "alice", adding_bytes=500)  # type: ignore[arg-type]
    assert (status.used_bytes, status.quota_bytes, status.state) == (500, 1000, "ok")
    with pytest.raises(OverQuota) as too_big:
        await quota.require_room(unit(500), USERS, "alice", adding_bytes=501)  # type: ignore[arg-type]
    assert too_big.value.extras == {"used_bytes": 500, "quota_bytes": 1000}
    assert "do not fit" in str(too_big.value)
    with pytest.raises(OverQuota) as over:
        await quota.require_room(unit(1000), USERS, "alice")  # type: ignore[arg-type]
    assert over.value.status == 507 and over.value.slug == "over-quota"
    unlimited = await quota.require_room(unit(), USERS, "root", adding_bytes=10**15)  # type: ignore[arg-type]
    assert unlimited.quota_bytes is None and unlimited.state == "ok"


async def test_remaining_bytes_and_the_manual_gate() -> None:
    assert await quota.remaining_bytes(unit(300), USERS, "alice") == 700  # type: ignore[arg-type]
    assert await quota.remaining_bytes(unit(3000), USERS, "alice") == 0  # type: ignore[arg-type]
    assert await quota.remaining_bytes(unit(), USERS, "root") is None  # type: ignore[arg-type]
    await quota.gate_manual(unit(950), USERS, "alice")  # type: ignore[arg-type]  # near passes
    with pytest.raises(OverQuota):
        await quota.gate_manual(unit(1000), USERS, "alice")  # type: ignore[arg-type]


async def test_over_quota_names_the_numbers() -> None:
    status = await quota.quota_status(unit(2_000_000), FakeUsers({"alice": 1_000_000}), "alice")  # type: ignore[arg-type]
    error = quota.over_quota(status)
    assert str(error) == (
        "over quota: 2.0 MB of 1.0 MB used; free some space or ask for a larger quota"
    )
    removed = await quota.quota_status(unit(5), FakeUsers({}), "alice")  # type: ignore[arg-type]
    assert removed.removed and quota.over_quota(removed).extras["quota_bytes"] == 5


async def test_on_manual_wanted_lifts_only_blocked_items() -> None:
    rows = [Row("a"), Row("b", blocked_reason=None)]
    uow = unit(rows=rows)
    await quota.on_manual_wanted(uow, ["a", "b", "missing"])  # type: ignore[arg-type]
    assert uow.quota.unblocked == [["a"]]
    assert [(e.item_id, e.state) for e in uow.events] == [("a", ArchiveState.wanted)]
    quiet = unit(rows=[Row("c", blocked_reason=None)])
    await quota.on_manual_wanted(quiet, ["c"])  # type: ignore[arg-type]
    await quota.on_manual_wanted(quiet, [])  # type: ignore[arg-type]
    assert quiet.quota.unblocked == [] and quiet.events == []


async def test_release_walks_in_order_within_the_budget() -> None:
    rows = [
        Row("unknown", wanted_reason=WantedReason.manual.value, blocked_bytes=None),
        Row("small", wanted_reason=WantedReason.follow.value, blocked_bytes=300),
        Row("large", blocked_bytes=500),
        Row("tiny", blocked_bytes=10),
    ]
    uow = unit(used=400, rows=rows)  # 600 left
    released = await quota.release_if_room(uow, USERS, "alice")  # type: ignore[arg-type]
    assert released == 2, "the walk stops at the first item that does not fit"
    assert uow.quota.unblocked == [["unknown", "small"]]
    assert uow.quota.limits == [quota.RELEASE_PER_TICK]
    assert uow.jobs.enqueued == [("unknown", 50), ("small", 100)]
    assert [e.item_id for e in uow.events if isinstance(e, ItemEvent)] == ["unknown", "small"]
    assert not [e for e in uow.events if isinstance(e, JobEvent)]
    assert uow.notified == 1


async def test_release_nothing_while_over_and_everything_when_unlimited() -> None:
    rows = [Row("a", blocked_bytes=None)]
    over = unit(used=1000, rows=rows)
    assert await quota.release_if_room(over, USERS, "alice") == 0  # type: ignore[arg-type]
    assert over.quota.limits == [] and over.notified == 0
    empty = unit(used=0)
    assert await quota.release_if_room(empty, USERS, "alice") == 0  # type: ignore[arg-type]
    assert empty.notified == 0

    big = [Row(f"i{n}", blocked_bytes=10**12) for n in range(3)]
    uow = FakeUow(quota=FakeQuota(blocked=big), catalog=FakeCatalog({r.id: r for r in big}))
    assert await quota.release_if_room(uow, USERS, "root") == 3  # type: ignore[arg-type]
    assert [priority for _, priority in uow.jobs.enqueued] == [200, 200, 200]


async def test_quota_statuses_list_configured_users_then_removed_owners() -> None:
    uow = FakeUow(quota=FakeQuota(used={"zed": 1, "alice": 2}), catalog=FakeCatalog({}))
    statuses = await quota.quota_statuses(uow, USERS)  # type: ignore[arg-type]
    assert [(s.username, s.removed) for s in statuses] == [
        ("alice", False),
        ("root", False),
        ("zed", True),
    ]


def test_wanted_priority() -> None:
    assert quota.wanted_priority(WantedReason.backfill.value) == 200
    assert quota.wanted_priority(WantedReason.follow.value) == 100
    assert quota.wanted_priority(WantedReason.manual.value) == 50
    assert quota.wanted_priority(WantedReason.request.value) == 50
    assert quota.wanted_priority(None) == 50
