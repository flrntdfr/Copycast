"""Telemetry: refresh runs, the worker heartbeat and the engine versions seen."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import EngineVersion, RefreshRun, WorkerHeartbeat
from copycast.domain.enums import JobTrigger, RefreshRunStatus


class TelemetryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ refresh runs

    async def start_refresh_run(
        self, feed_id: str, *, trigger: JobTrigger, job_id: uuid.UUID | None = None
    ) -> RefreshRun:
        run = RefreshRun(
            feed_id=feed_id,
            job_id=job_id,
            trigger=trigger.value,
            status=RefreshRunStatus.running.value,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish_refresh_run(
        self,
        run_id: int,
        status: RefreshRunStatus,
        *,
        listed_count: int = 0,
        new_count: int = 0,
        delisted_count: int = 0,
        wanted_count: int = 0,
        error: str | None = None,
        engine_version: str | None = None,
    ) -> None:
        await self._session.execute(
            update(RefreshRun)
            .where(RefreshRun.id == run_id)
            .values(
                status=status.value,
                finished_at=func.now(),
                listed_count=listed_count,
                new_count=new_count,
                delisted_count=delisted_count,
                wanted_count=wanted_count,
                error=error,
                engine_version=engine_version,
            )
        )
        instance = self._session.identity_map.get((RefreshRun, (run_id,), None))
        if instance is not None:
            await self._session.refresh(instance)

    async def refresh_runs(self, feed_id: str, *, limit: int = 50) -> list[RefreshRun]:
        result = await self._session.execute(
            select(RefreshRun)
            .where(RefreshRun.feed_id == feed_id)
            .order_by(RefreshRun.started_at.desc(), RefreshRun.id.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def purge_refresh_runs(self, before: datetime) -> int:
        result = await self._session.execute(
            delete(RefreshRun).where(RefreshRun.started_at < before)
        )
        return rows_affected(result)

    # ------------------------------------------------------------------ heartbeat

    async def heartbeat(self, worker_id: str, status: dict[str, Any]) -> None:
        stmt = insert(WorkerHeartbeat).values(
            worker_id=worker_id, seen_at=func.now(), status=status
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[WorkerHeartbeat.worker_id],
                set_={"seen_at": func.now(), "status": status},
            )
        )

    async def latest_heartbeat(self) -> WorkerHeartbeat | None:
        result = await self._session.execute(
            select(WorkerHeartbeat).order_by(WorkerHeartbeat.seen_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ engine versions

    async def record_engine_version(
        self,
        version: str,
        *,
        channel: str | None = None,
        release_date: date | None = None,
        git_head: str | None = None,
        app_version: str | None = None,
    ) -> EngineVersion:
        stmt = insert(EngineVersion).values(
            version=version,
            channel=channel,
            release_date=release_date,
            git_head=git_head,
            app_version=app_version,
        )
        await self._session.execute(
            stmt.on_conflict_do_nothing(index_elements=[EngineVersion.version])
        )
        result = await self._session.execute(
            select(EngineVersion).where(EngineVersion.version == version)
        )
        return result.scalar_one()

    async def engine_versions(self) -> list[EngineVersion]:
        result = await self._session.execute(
            select(EngineVersion).order_by(EngineVersion.first_seen_at.desc())
        )
        return list(result.scalars())


__all__ = ["TelemetryRepository"]
