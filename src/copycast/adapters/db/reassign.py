"""``copycast users reassign OLD NEW``: give everything one owner has to another User.

The way out of the mode-switch and legacy-rename traps (§3.3): Feeds whose owner is no
longer configured answer 401 on their pairs and keys until they belong to a configured
User again. Under ``WORKER_LOCK``, like ``copycast rebuild`` (refused while the worker
runs), and in one transaction:

1. refused, changing nothing, when NEW is not configured, when OLD is NEW, or when one of
   OLD's Mirrors tracks a Source one of NEW's already does (every clashing pair listed);
2. OLD's default Local feed, if any, becomes an ordinary Local feed of NEW (NEW keeps hers);
3. ``feeds.owner``, ``api_keys.owner`` and ``jobs.requested_by`` move from OLD to NEW, and
   each moved Feed's ``intent_version`` is bumped;

then, after the commit, every moved Feed's ``feed.json`` is exported again (a command
writing SQL has no unit-of-work hooks), and ``users/OLD/`` moves to ``users/NEW/`` in the
Object store (``adapters.storage.user_settings.move_user_settings``: NEW's files win).
Feed ids, pairs and media keys never change. OLD's ``users``, ``sessions`` and
``login_attempts`` rows are left alone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
from copycast.adapters.db.migrate import schema_is_current
from copycast.adapters.db.models import ApiKey, Feed, Job, User
from copycast.adapters.db.session import create_db_engine, create_sessionmaker
from copycast.adapters.storage.descriptor import export_descriptor
from copycast.adapters.storage.layout import Layout
from copycast.adapters.storage.objects import open_store
from copycast.adapters.storage.user_settings import move_user_settings
from copycast.application.users import UserDirectory
from copycast.domain.exceptions import Conflict
from copycast.domain.users import is_valid_username
from copycast.logging import get_logger
from copycast.settings import Settings, SettingsError

log = get_logger(__name__)

WORKER_RUNNING = (
    "the worker is running (WORKER_LOCK is held); stop it, run the command, then start it again"
)


class ReassignRefused(Conflict):
    """Nothing was changed: the reason is the message (clashing Sources listed)."""


@dataclass(frozen=True, slots=True)
class DedupClash:
    """One of OLD's Mirrors and one of NEW's that track the same Source."""

    old_feed_id: str
    new_feed_id: str
    dedup_key: str


@dataclass(frozen=True)
class ReassignReport:
    """What ``reassign`` moved."""

    old: str
    new: str
    feeds: list[str] = field(default_factory=list[str])
    keys: int = 0
    jobs: int = 0
    demoted_default: str | None = None
    """OLD's default Local feed, now an ordinary Local feed of NEW."""
    files: list[str] = field(default_factory=list[str])
    """The ``users/OLD/`` keys moved to ``users/NEW/``."""
    conflicts: list[str] = field(default_factory=list[str])
    """The ``users/OLD/`` keys kept because NEW has its own."""


async def dedup_clashes(session: AsyncSession, old: str, new: str) -> list[DedupClash]:
    """Every pair of OLD's and NEW's Mirrors with the same ``source_dedup_key``."""
    mine, theirs = aliased(Feed), aliased(Feed)
    rows = await session.execute(
        select(mine.id, theirs.id, mine.source_dedup_key)
        .join(theirs, theirs.source_dedup_key == mine.source_dedup_key)
        .where(mine.owner == old, theirs.owner == new, mine.source_dedup_key.is_not(None))
        .order_by(mine.id)
    )
    return [DedupClash(o, n, key) for o, n, key in rows.tuples() if key is not None]


async def reassign_rows(session: AsyncSession, old: str, new: str) -> ReassignReport:
    """Steps 1 to 3 in ``session``'s transaction (the caller commits); raises
    :class:`ReassignRefused` before writing anything on a dedup-key clash."""
    clashes = await dedup_clashes(session, old, new)
    if clashes:
        pairs = "; ".join(
            f"{c.old_feed_id} ({old}) and {c.new_feed_id} ({new}) both track {c.dedup_key}"
            for c in clashes
        )
        raise ReassignRefused(
            f"{old} and {new} mirror the same Source; delete one of each pair first: {pairs}"
        )
    # NEW is configured but its row may predate no restart yet: the foreign keys need it.
    await session.execute(insert(User).values(username=new).on_conflict_do_nothing())
    demoted = await session.scalar(
        update(Feed)
        .where(Feed.owner == old, Feed.is_default.is_(True))
        .values(is_default=False)
        .returning(Feed.id)
    )
    moved = await session.scalars(
        update(Feed)
        .where(Feed.owner == old)
        .values(owner=new, intent_version=Feed.intent_version + 1)
        .returning(Feed.id)
    )
    feeds = sorted(moved.all())
    keys = await session.execute(update(ApiKey).where(ApiKey.owner == old).values(owner=new))
    jobs = await session.execute(
        update(Job).where(Job.requested_by == old).values(requested_by=new)
    )
    return ReassignReport(
        old=old,
        new=new,
        feeds=feeds,
        keys=_rowcount(keys),
        jobs=_rowcount(jobs),
        demoted_default=demoted,
    )


def _rowcount(result: object) -> int:
    count = getattr(result, "rowcount", 0)
    return int(count) if isinstance(count, int) else 0


def check_names(settings: Settings, old: str, new: str) -> None:
    """The refusals that need no database: NEW configured, OLD a name, OLD not NEW."""
    if old == new:
        raise ReassignRefused(f"{old} and {new} are the same User")
    if not is_valid_username(old):
        raise ReassignRefused(f"{old!r} is not a username")
    if not UserDirectory(settings).is_configured(new):
        raise ReassignRefused(f"{new} is not a configured User; add it to [[auth.users]] first")


async def reassign(
    settings: Settings,
    old: str,
    new: str,
    *,
    db_engine: AsyncEngine | None = None,
    layout: Layout | None = None,
) -> ReassignReport:
    """Give everything OLD owns to NEW (see the module docstring)."""
    check_names(settings, old, new)
    layout = layout or Layout(open_store(settings.storage))
    own_engine = db_engine is None
    engine = db_engine or create_db_engine(settings, application_name="copycast-reassign")
    try:
        if not await schema_is_current(engine):
            raise SettingsError("the database schema is not current; run `copycast migrate`")
        async with SessionLock(engine, WORKER_LOCK) as lock:
            if not lock.held:
                raise ReassignRefused(WORKER_RUNNING)
            return await _reassign(create_sessionmaker(engine), layout, old, new)
    finally:
        if own_engine:
            await engine.dispose()


async def _reassign(
    sessionmaker: async_sessionmaker[AsyncSession], layout: Layout, old: str, new: str
) -> ReassignReport:
    async with sessionmaker() as session, session.begin():
        report = await reassign_rows(session, old, new)
    async with sessionmaker() as session:
        for feed_id in report.feeds:
            await export_descriptor(session, layout, feed_id, force=True)
    moved = await asyncio.to_thread(move_user_settings, layout, old, new)
    log.info(
        "users.reassigned",
        old=old,
        new=new,
        feeds=len(report.feeds),
        keys=report.keys,
        jobs=report.jobs,
        demoted_default=report.demoted_default,
        files=len(moved.copied),
        conflicts=moved.conflicts,
    )
    return ReassignReport(
        old=old,
        new=new,
        feeds=report.feeds,
        keys=report.keys,
        jobs=report.jobs,
        demoted_default=report.demoted_default,
        files=moved.copied,
        conflicts=moved.conflicts,
    )


__all__ = [
    "WORKER_RUNNING",
    "DedupClash",
    "ReassignRefused",
    "ReassignReport",
    "check_names",
    "dedup_clashes",
    "reassign",
    "reassign_rows",
]
