"""``copycast users reassign OLD NEW``: the database and the Object store (§1.19).

Over the world (Bob's Mirror, Local feed, default Local feed, key and job; Alice's default
Local feed), Bob's things move to Alice.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
from copycast.adapters.db.models import ApiKey, Feed, Job
from copycast.adapters.db.reassign import WORKER_RUNNING, ReassignRefused, reassign
from copycast.adapters.storage.layout import Layout
from copycast.cli import main
from copycast.domain.enums import JobKind
from copycast.settings import Settings
from tests.integration.storage.conftest import job_row, mirror_row, seed
from tests.support.world import World

pytestmark = pytest.mark.integration

YOUTUBE = ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tbobs-secret\n"
ALICES_YOUTUBE = ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\talices-secret\n"
TWITCH = ".twitch.tv\tTRUE\t/\tTRUE\t1790000000\tauth-token\tbobs-twitch\n"


async def _feeds(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, Feed]:
    async with sessionmaker() as session:
        rows = (await session.scalars(select(Feed))).all()
        return {row.id: row for row in rows}


async def _bobs_job(sessionmaker: async_sessionmaker[AsyncSession], world: World) -> uuid.UUID:
    """A job Bob requested (stamping ``requested_by`` on enqueue is not this group's)."""
    async with sessionmaker() as session, session.begin():
        feed = await session.get(Feed, world.mirror_id)
        assert feed is not None
        job = job_row(JobKind.refresh, feed=feed, requested_by="bob", dedup_key=None)
        session.add(job)
        await session.flush()
        return job.id


def _seed_settings(layout: Layout) -> None:
    """Bob's Defaults and two Cookie files; Alice has her own YouTube file."""
    store = layout.store
    store.put_bytes("users/bob/defaults.json", '{"language": "fr"}\n')
    store.put_bytes("users/bob/cookie-files/yt.txt", YOUTUBE)
    store.put_bytes("users/bob/cookie-files/twitch.txt", TWITCH)
    store.put_bytes("users/bob/cookies.txt", YOUTUBE + TWITCH)
    store.put_bytes("users/alice/cookie-files/yt.txt", ALICES_YOUTUBE)
    store.put_bytes("users/alice/cookies.txt", ALICES_YOUTUBE)


async def test_reassign_moves_everything_and_keeps_news_files(
    world: World,
    users_settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    layout: Layout,
) -> None:
    job_id = await _bobs_job(sessionmaker, world)
    _seed_settings(layout)
    before = await _feeds(sessionmaker)
    bobs = sorted(fid for fid, row in before.items() if row.owner == "bob")
    assert world.mirror_id in bobs and world.defaults["bob"] in bobs

    report = await reassign(users_settings, "bob", "alice", db_engine=db_engine, layout=layout)

    assert report.feeds == bobs
    assert report.keys == 1 and report.jobs == 1
    assert report.demoted_default == world.defaults["bob"]
    after = await _feeds(sessionmaker)
    assert {after[fid].owner for fid in bobs} == {"alice"}
    for fid in bobs:  # each moved Feed's intent is bumped; ids never change
        assert after[fid].intent_version == before[fid].intent_version + 1
    # Bob's default is now an ordinary Local feed of Alice's; Alice keeps hers.
    assert after[world.defaults["bob"]].is_default is False
    assert after[world.defaults["alice"]].is_default is True
    assert after[world.defaults["alice"]].owner == "alice"
    async with sessionmaker() as session:
        key = await session.scalar(select(ApiKey).where(ApiKey.id == uuid.UUID(world.key_id)))
        job = await session.get(Job, job_id)
    assert key is not None and key.owner == "alice"
    assert job is not None and job.requested_by == "alice"
    # feed.json says so too (exported after the commit).
    for fid in bobs:
        descriptor: Any = layout.read_json(layout.descriptor_key(fid))
        assert descriptor["feed"]["owner"] == "alice", fid

    # users/bob/ -> users/alice/: what Alice lacked moved, what she had stays hers.
    store = layout.store
    assert store.get_text("users/alice/defaults.json") == '{"language": "fr"}\n'
    assert not store.exists("users/bob/defaults.json")
    assert TWITCH in (store.get_text("users/alice/cookie-files/twitch.txt") or "")
    assert store.get_text("users/alice/cookie-files/yt.txt") == ALICES_YOUTUBE
    jar = store.get_text("users/alice/cookies.txt") or ""
    assert "bobs-twitch" in jar and "alices-secret" in jar and "bobs-secret" not in jar
    # Bob's conflicting file is left in place (nothing is lost), with his jar merged anew.
    assert report.conflicts == ["users/bob/cookie-files/yt.txt"]
    assert store.get_text("users/bob/cookie-files/yt.txt") == YOUTUBE
    assert not store.exists("users/bob/cookie-files/twitch.txt")
    assert "bobs-twitch" not in (store.get_text("users/bob/cookies.txt") or "")
    assert sorted(report.files) == [
        "users/bob/cookie-files/twitch.txt",
        "users/bob/defaults.json",
    ]


async def test_a_shared_source_refuses_and_lists_every_pair(
    world: World,
    users_settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    uow_factory: Any,
    layout: Layout,
) -> None:
    alices = mirror_row(world.source_url, id="a1a1a1a1a1a1a1a1", owner="alice")
    async with uow_factory() as uow:
        await seed(uow, alices)
    layout.store.put_bytes("users/bob/defaults.json", "{}\n")
    before = await _feeds(sessionmaker)

    with pytest.raises(ReassignRefused) as refused:
        await reassign(users_settings, "bob", "alice", db_engine=db_engine, layout=layout)

    message = str(refused.value)
    assert world.mirror_id in message and "a1a1a1a1a1a1a1a1" in message
    assert alices.source_dedup_key is not None and alices.source_dedup_key in message
    after = await _feeds(sessionmaker)
    assert {fid: row.owner for fid, row in after.items()} == {
        fid: row.owner for fid, row in before.items()
    }
    assert after[world.defaults["bob"]].is_default is True
    assert layout.store.exists("users/bob/defaults.json")
    assert not layout.store.exists("users/alice/defaults.json")


async def test_new_must_be_configured_and_differ_from_old(
    world: World, users_settings: Settings, db_engine: AsyncEngine, layout: Layout
) -> None:
    del world
    with pytest.raises(ReassignRefused, match="carol is not a configured User"):
        await reassign(users_settings, "bob", "carol", db_engine=db_engine, layout=layout)
    with pytest.raises(ReassignRefused, match="same User"):
        await reassign(users_settings, "bob", "bob", db_engine=db_engine, layout=layout)


async def test_refused_while_the_worker_runs(
    world: World,
    users_settings: Settings,
    db_engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
    layout: Layout,
) -> None:
    async with SessionLock(db_engine, WORKER_LOCK) as lock:
        assert lock.held
        with pytest.raises(ReassignRefused, match="WORKER_LOCK is held"):
            await reassign(users_settings, "bob", "alice", db_engine=db_engine, layout=layout)
    assert str(ReassignRefused(WORKER_RUNNING)).startswith("the worker is running")
    feeds = await _feeds(sessionmaker)
    assert feeds[world.mirror_id].owner == "bob"


async def test_a_removed_owner_moves_to_a_configured_user_through_the_cli(
    users_settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    uow_factory: Any,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The mode-switch trap: Feeds still owned by the open-mode Admin "copycast", who is not
    # configured in users mode.
    feed = mirror_row()
    async with uow_factory() as uow:
        await seed(uow, feed)
    layout.store.put_bytes("users/copycast/defaults.json", '{"language": "it"}\n')
    monkeypatch.setattr("copycast.cli._load_settings", lambda: users_settings)

    # In a thread: the command runs its own event loop.
    result = await asyncio.to_thread(
        CliRunner().invoke, main, ["users", "reassign", "copycast", "root", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert "moved from copycast to root: 1 Feeds" in result.output
    async with sessionmaker() as session:
        owner = await session.scalar(select(Feed.owner).where(Feed.id == feed.id))
    assert owner == "root"
    assert layout.store.get_text("users/root/defaults.json") == '{"language": "it"}\n'
