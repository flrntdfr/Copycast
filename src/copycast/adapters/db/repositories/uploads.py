"""Staged uploads: each Owner's count and bytes under ``uploads/`` (see the model)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import StagedUpload
from copycast.application.services.context import StagedUsage


class StagedUploadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, upload_id: str, owner: str, *, declared: int, now: datetime) -> None:
        await self._session.execute(
            insert(StagedUpload).values(
                id=upload_id, owner=owner, received=False, bytes=declared, created_at=now
            )
        )

    async def usage(
        self,
        owner: str,
        *,
        live_after: datetime,
        held_after: datetime,
        exclude: str | None = None,
    ) -> StagedUsage:
        """``owner``'s staged uploads created after ``live_after`` (younger than the TTL),
        and their bytes: a received one's size, a pending one's declared size while it
        was created after ``held_after``, else nothing."""
        counted = case(
            (StagedUpload.received, StagedUpload.bytes),
            (StagedUpload.created_at > held_after, StagedUpload.bytes),
            else_=0,
        )
        conditions = [StagedUpload.owner == owner, StagedUpload.created_at > live_after]
        if exclude is not None:
            conditions.append(StagedUpload.id != exclude)
        row = (
            await self._session.execute(
                select(func.count(), func.coalesce(func.sum(counted), 0)).where(and_(*conditions))
            )
        ).one()
        return StagedUsage(count=int(row[0]), bytes=int(row[1]))

    async def receive(self, upload_id: str, owner: str, *, size: int, now: datetime) -> None:
        """The upload's PUT completed: it counts by ``size`` from now on (a row is made for
        an upload staged before the table existed, or across a rebuild)."""
        await self._session.execute(
            insert(StagedUpload)
            .values(id=upload_id, owner=owner, received=True, bytes=size, created_at=now)
            .on_conflict_do_update(
                index_elements=[StagedUpload.id], set_={"received": True, "bytes": size}
            )
        )

    async def release(self, upload_id: str) -> None:
        """A pending upload whose PUT failed or was refused holds nothing any more."""
        await self._session.execute(
            update(StagedUpload)
            .where(StagedUpload.id == upload_id, StagedUpload.received.is_(False))
            .values(bytes=0)
        )

    async def delete(self, upload_ids: Collection[str]) -> int:
        if not upload_ids:
            return 0
        result = await self._session.execute(
            delete(StagedUpload).where(StagedUpload.id.in_(list(upload_ids)))
        )
        return rows_affected(result)

    async def delete_expired(self, before: datetime) -> int:
        result = await self._session.execute(
            delete(StagedUpload).where(StagedUpload.created_at <= before)
        )
        return rows_affected(result)


__all__ = ["StagedUploadRepository"]
