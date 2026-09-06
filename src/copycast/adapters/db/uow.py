"""The unit of work: one session, one transaction, the repositories, and after-commit hooks.

``async with factory() as uow:`` commits on a clean exit and rolls back on an
exception. Hooks registered with :meth:`UnitOfWork.after_commit` run once,
after the commit succeeded; :meth:`UnitOfWork.update_intent` bumps the feed's
counters and schedules the descriptor export as such a hook.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Self

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from copycast.adapters.db.repositories import (
    ApiKeyRepository,
    AssetRepository,
    CatalogRepository,
    FeedRepository,
    JobRepository,
    RequestRepository,
    TelemetryRepository,
)
from copycast.adapters.storage.descriptor import (
    DEBOUNCE_SECONDS,
    DebouncedExporter,
    DescriptorExporter,
)
from copycast.adapters.storage.layout import Layout
from copycast.application.events import CHANNEL, JOBS_CHANNEL, serialize
from copycast.domain.exceptions import ConcurrentUpdate
from copycast.logging import get_logger

Hook = Callable[[], Awaitable[None] | None]

log = get_logger(__name__)

#: SQLSTATE codes Postgres raises when it aborts one of two racing transactions:
#: ``40001`` serialization_failure, ``40P01`` deadlock_detected. Both are safe to retry.
RETRYABLE_SQLSTATES: frozenset[str] = frozenset({"40001", "40P01"})


def is_retryable(exc: BaseException) -> bool:
    """True when ``exc`` is a driver error carrying a retryable SQLSTATE."""
    orig = getattr(exc, "orig", None) if isinstance(exc, DBAPIError) else exc
    sqlstate = getattr(orig, "sqlstate", None)
    return isinstance(sqlstate, str) and sqlstate in RETRYABLE_SQLSTATES


class UnitOfWork:
    def __init__(
        self,
        session: AsyncSession,
        layout: Layout,
        *,
        exporter: DescriptorExporter,
        debounced_exporter: DescriptorExporter | None = None,
    ) -> None:
        self.session = session
        self.layout = layout
        self._exporter = exporter
        self._debounced = debounced_exporter
        self._hooks: list[Hook] = []
        self._exports: dict[str, bool] = {}
        self._committed = False
        self._closed = False
        self.feeds = FeedRepository(session)
        self.catalog = CatalogRepository(session)
        self.assets = AssetRepository(session)
        self.requests = RequestRepository(session)
        self.jobs = JobRepository(session)
        self.telemetry = TelemetryRepository(session)
        self.api_keys = ApiKeyRepository(session)

    # ------------------------------------------------------------------ hooks

    def after_commit(self, hook: Hook) -> None:
        """Run ``hook`` (sync or async) once the transaction has committed; dropped on rollback."""
        self._hooks.append(hook)

    def export_after_commit(self, feed_id: str, *, debounce: bool = False) -> None:
        """Export ``feed.json`` of ``feed_id`` after commit, once per feed and transaction.

        ``debounce`` routes through the per-feed 5 s debounce (archive jobs).
        An immediate request wins over a debounced one for the same feed.
        """
        self._exports[feed_id] = self._exports.get(feed_id, True) and debounce

    async def update_intent(self, feed_id: str, *, debounce: bool = False) -> tuple[int, int]:
        """Bump ``intent_version`` and ``revision`` and schedule the export."""
        versions = await self.feeds.update_intent(feed_id)
        self.export_after_commit(feed_id, debounce=debounce)
        return versions

    async def bump_revision(self, feed_id: str) -> int:
        return await self.feeds.bump_revision(feed_id)

    # ------------------------------------------------------------------ notifications

    async def publish(self, event: BaseModel) -> None:
        """``NOTIFY copycast_events`` inside this transaction: delivered only on commit."""
        await self.session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": CHANNEL, "payload": serialize(event)},
        )

    async def notify_jobs(self, payload: str = "") -> None:
        """Wake the worker's claim loop (``NOTIFY copycast_jobs``) once this commits."""
        await self.session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": JOBS_CHANNEL, "payload": payload},
        )

    # ------------------------------------------------------------------ lifecycle

    async def flush(self) -> None:
        await self.session.flush()

    async def commit(self) -> None:
        """Commit, then run the hooks; hook failures are logged, never raised."""
        if self._committed:
            return
        try:
            await self.session.commit()
        except DBAPIError as exc:
            await self.rollback()
            if is_retryable(exc):
                raise ConcurrentUpdate() from exc
            raise
        self._committed = True
        hooks, self._hooks = self._hooks, []
        exports, self._exports = self._exports, {}
        for feed_id, debounce in exports.items():
            exporter = self._debounced if debounce and self._debounced else self._exporter
            try:
                await exporter.export(feed_id)
            except Exception:
                log.exception("descriptor.export_failed", feed_id=feed_id)
        for hook in hooks:
            try:
                outcome = hook()
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:
                log.exception("uow.after_commit_failed", hook=getattr(hook, "__name__", repr(hook)))

    async def rollback(self) -> None:
        self._hooks.clear()
        self._exports.clear()
        await self.session.rollback()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self.session.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self.commit()
            else:
                await self.rollback()
                if exc is not None and is_retryable(exc):
                    raise ConcurrentUpdate() from exc
        finally:
            await self.close()


class UnitOfWorkFactory:
    """``async with factory() as uow:`` opens one transaction over the shared sessionmaker."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        layout: Layout,
        *,
        exporter: DescriptorExporter | None = None,
        debounce_seconds: float | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.layout = layout
        self.exporter: DescriptorExporter = exporter or DescriptorExporter(sessionmaker, layout)
        self.debounced_exporter: DescriptorExporter = DebouncedExporter(
            sessionmaker,
            layout,
            delay=DEBOUNCE_SECONDS if debounce_seconds is None else debounce_seconds,
        )

    def __call__(self) -> UnitOfWork:
        return UnitOfWork(
            self.sessionmaker(),
            self.layout,
            exporter=self.exporter,
            debounced_exporter=self.debounced_exporter,
        )

    async def flush_exports(self) -> None:
        """Export every debounced feed now (worker shutdown)."""
        await self.debounced_exporter.flush()

    async def aclose(self) -> None:
        await self.debounced_exporter.aclose()
        await self.exporter.aclose()


def uow_of(obj: Any) -> UnitOfWork:
    """Narrow an untyped container attribute to :class:`UnitOfWork` (tests and services)."""
    if not isinstance(obj, UnitOfWork):
        raise TypeError(f"expected a UnitOfWork, got {type(obj).__name__}")
    return obj


__all__ = [
    "RETRYABLE_SQLSTATES",
    "Hook",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "is_retryable",
    "uow_of",
]
