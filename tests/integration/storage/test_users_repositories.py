"""Users, sessions, login attempts and storage per Owner (migration 0017's tables)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from copycast.adapters.db.uow import UnitOfWorkFactory
from copycast.application.events import FeedEvent
from copycast.application.services.auth import LOCK_THRESHOLD
from copycast.application.services.context import AttemptClaim, Scope
from copycast.domain.credentials import mint_session_token, session_digest
from tests.integration.storage.conftest import OWNER, bucket_row, mirror_row

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


async def test_users_are_ensured_never_deleted(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.users.ensure(["alice", "bob", "alice"])
        await uow.users.ensure([])
    async with uow_factory() as uow:
        assert [u.username for u in await uow.users.all()] == ["alice", "bob", OWNER]
        await uow.users.touch_login("alice", NOW)
    async with uow_factory() as uow:
        alice = next(u for u in await uow.users.all() if u.username == "alice")
        assert alice.last_login_at == NOW and alice.first_seen_at is not None


async def test_a_totp_step_is_claimed_once(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.users.ensure(["alice"])
        assert await uow.users.totp_last_step("alice") is None
        assert await uow.users.claim_totp_step("alice", 100) is True
        assert await uow.users.claim_totp_step("alice", 100) is False
        assert await uow.users.claim_totp_step("alice", 99) is False
        assert await uow.users.claim_totp_step("alice", 101) is True
        assert await uow.users.totp_last_step("alice") == 101
        assert await uow.users.totp_last_step("nobody") is None


async def test_credential_digests(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.users.ensure(["alice"])
        await uow.users.claim_totp_step("alice", 5)
        assert (await uow.users.credential_digests())["alice"] is None
        await uow.users.set_credential_digest("alice", b"x" * 32, reset_totp=False)
        assert await uow.users.totp_last_step("alice") == 5
        await uow.users.set_credential_digest("alice", b"y" * 32, reset_totp=True)
    async with uow_factory() as uow:
        digests = await uow.users.credential_digests()
        assert digests["alice"] == b"y" * 32 and digests[OWNER] is None
        assert await uow.users.totp_last_step("alice") is None


async def test_sessions(uow_factory: UnitOfWorkFactory) -> None:
    tokens = [mint_session_token() for _ in range(3)]
    async with uow_factory() as uow:
        await uow.users.ensure(["alice", "bob"])
        await uow.sessions.add(
            session_digest(tokens[0]),
            "alice",
            now=NOW,
            expires_at=NOW + timedelta(days=30),
            user_agent="Firefox",
        )
        await uow.sessions.add(
            session_digest(tokens[1]), "bob", now=NOW, expires_at=NOW, user_agent=None
        )
        await uow.sessions.add(
            session_digest(tokens[2]),
            OWNER,
            now=NOW,
            expires_at=NOW + timedelta(days=1),
            user_agent=None,
        )
    async with uow_factory() as uow:
        found = await uow.sessions.get(session_digest(tokens[0]))
        assert found is not None and found.username == "alice" and found.user_agent == "Firefox"
        assert found.created_at == found.last_seen_at == NOW
        later = NOW + timedelta(minutes=5)
        await uow.sessions.touch(
            session_digest(tokens[0]), now=later, expires_at=later + timedelta(days=30)
        )
        assert await uow.sessions.get(session_digest("nope")) is None
        assert await uow.sessions.delete_expired(NOW + timedelta(seconds=1)) == 1
        assert await uow.sessions.delete_for_users_not_in(["alice"]) == 1
    async with uow_factory() as uow:
        kept = await uow.sessions.get(session_digest(tokens[0]))
        assert kept is not None and kept.last_seen_at == NOW + timedelta(minutes=5)
        assert await uow.sessions.delete_for_user("alice") == 1
        await uow.sessions.delete(session_digest(tokens[0]))
        assert await uow.sessions.get(session_digest(tokens[0])) is None


async def test_login_attempts_lock_from_the_fifth_claim(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        for _ in range(4):
            assert await uow.login_attempts.claim("ghost", NOW) == AttemptClaim(True, None)
        # The fifth attempt may still run, and locks the name for whoever comes next.
        assert await uow.login_attempts.claim("ghost", NOW) == AttemptClaim(
            True, NOW + timedelta(seconds=30)
        )
        # Locked: refused, and nothing more is counted.
        assert await uow.login_attempts.claim("ghost", NOW) == AttemptClaim(
            False, NOW + timedelta(seconds=30)
        )
        later = NOW + timedelta(seconds=31)
        assert await uow.login_attempts.claim("ghost", later) == AttemptClaim(
            True, later + timedelta(seconds=60)
        )
        row = await uow.login_attempts.get("ghost")
        assert row is not None and row.failed_count == 6
        await uow.login_attempts.claim("old", NOW - timedelta(hours=25))
        await uow.login_attempts.claim("recent", NOW - timedelta(hours=1))
    async with uow_factory() as uow:
        # Locked rows and recent rows stay; an old unlocked one goes.
        assert await uow.login_attempts.prune(NOW) == 1
        assert await uow.login_attempts.get("old") is None
        assert await uow.login_attempts.get("recent") is not None
        await uow.login_attempts.reset("ghost")
        assert await uow.login_attempts.get("ghost") is None


async def test_concurrent_claims_never_pass_the_lock(uow_factory: UnitOfWorkFactory) -> None:
    """Regression: the lock check and the count are one step, whatever the concurrency.

    Each claim has its own transaction (as logins in several processes would); exactly
    as many get through as the threshold allows, the rest see the lock.
    """

    async def claim() -> AttemptClaim:
        async with uow_factory() as uow:
            return await uow.login_attempts.claim("alice", NOW)

    claims = await asyncio.gather(*(claim() for _ in range(18)))
    assert sum(c.allowed for c in claims) == LOCK_THRESHOLD
    assert all(c.locked_until == NOW + timedelta(seconds=30) for c in claims if not c.allowed)
    async with uow_factory() as uow:
        row = await uow.login_attempts.get("alice")
        assert row is not None and row.failed_count == LOCK_THRESHOLD


async def test_storage_per_owner(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.users.ensure(["alice"])
        await uow.feeds.add(mirror_row(storage_bytes=100))
        await uow.feeds.add(bucket_row("Later", storage_bytes=20))
        await uow.feeds.add(bucket_row("Hers", owner="alice", storage_bytes=7))
    async with uow_factory() as uow:
        assert await uow.quota.owned_storage_bytes(OWNER) == 120
        assert await uow.quota.owned_storage_bytes("alice") == 7
        assert await uow.quota.owned_storage_bytes("nobody") == 0
        assert await uow.quota.storage_by_owner() == {OWNER: 120, "alice": 7}


async def test_default_local_feed_is_one_per_owner(uow_factory: UnitOfWorkFactory) -> None:
    async with uow_factory() as uow:
        await uow.users.ensure(["alice"])
        mine = await uow.feeds.ensure_default_bucket(OWNER)
        hers = await uow.feeds.ensure_default_bucket("alice")
    assert mine.id != hers.id and mine.owner == OWNER and hers.owner == "alice"
    async with uow_factory() as uow:
        assert (await uow.feeds.ensure_default_bucket("alice")).id == hers.id
        default = await uow.feeds.default_bucket(OWNER)
        assert default is not None and default.id == mine.id


async def test_the_factory_hands_its_scope_to_the_unit_of_work(
    uow_factory: UnitOfWorkFactory,
) -> None:
    async with uow_factory() as uow:
        assert uow.scope is Scope.SYSTEM
    scope = Scope("alice", "alice")
    async with uow_factory(scope) as uow:
        assert uow.scope == scope
        # The owner argument is accepted (routing by owner arrives with the scoping work).
        await uow.publish(FeedEvent(feed_id="x", revision=1, reason="updated"), owner="alice")
