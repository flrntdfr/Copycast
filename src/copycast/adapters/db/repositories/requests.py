"""Requests: URLs pushed into an Inbox and the items they produced."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import utcnow
from copycast.adapters.db.models import Request, RequestItem
from copycast.domain.enums import RequestedVia, RequestStatus
from copycast.domain.exceptions import NotFound


class RequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, request_id: uuid.UUID) -> Request | None:
        return await self._session.get(Request, request_id)

    async def require(self, request_id: uuid.UUID, feed_id: str | None = None) -> Request:
        request = await self.get(request_id)
        if request is None or (feed_id is not None and request.feed_id != feed_id):
            raise NotFound("request", str(request_id))
        return request

    async def add(
        self,
        feed_id: str,
        url: str,
        via: RequestedVia,
        *,
        request_id: uuid.UUID | None = None,
    ) -> Request:
        request = Request(
            id=request_id or uuid.uuid4(),
            feed_id=feed_id,
            url=url,
            requested_via=via.value,
            status=RequestStatus.queued.value,
        )
        self._session.add(request)
        await self._session.flush()
        return request

    async def for_feed(self, feed_id: str) -> list[Request]:
        result = await self._session.execute(
            select(Request)
            .where(Request.feed_id == feed_id)
            .order_by(Request.created_at, Request.id)
        )
        return list(result.scalars())

    async def list_page(
        self, feed_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[list[Request], int]:
        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Request).where(Request.feed_id == feed_id)
                )
            ).scalar_one()
        )
        rows = await self._session.execute(
            select(Request)
            .where(Request.feed_id == feed_id)
            .order_by(Request.created_at.desc(), Request.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.scalars()), total

    async def count_for_feeds(self, feed_ids: Iterable[str]) -> dict[str, int]:
        ids = list(dict.fromkeys(feed_ids))
        if not ids:
            return {}
        rows = await self._session.execute(
            select(Request.feed_id, func.count())
            .where(Request.feed_id.in_(ids))
            .group_by(Request.feed_id)
        )
        counts = dict.fromkeys(ids, 0)
        for feed_id, count in rows:
            counts[feed_id] = int(count)
        return counts

    async def link_items(self, request_id: uuid.UUID, item_ids: Iterable[str]) -> int:
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return 0
        stmt = (
            insert(RequestItem)
            .values([{"request_id": request_id, "item_id": iid} for iid in ids])
            .on_conflict_do_nothing()
            .returning(RequestItem.item_id)
        )
        # RETURNING lists only the rows actually inserted (rowcount is -1 for
        # multi-row inserts on psycopg), so its length is the number linked.
        return len(list((await self._session.execute(stmt)).scalars()))

    async def item_ids(self, request_id: uuid.UUID) -> list[str]:
        result = await self._session.execute(
            select(RequestItem.item_id)
            .where(RequestItem.request_id == request_id)
            .order_by(RequestItem.created_at, RequestItem.item_id)
        )
        return list(result.scalars())

    async def item_ids_many(self, request_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        ids = list(dict.fromkeys(request_ids))
        grouped: dict[uuid.UUID, list[str]] = {rid: [] for rid in ids}
        if not ids:
            return grouped
        rows = await self._session.execute(
            select(RequestItem.request_id, RequestItem.item_id)
            .where(RequestItem.request_id.in_(ids))
            .order_by(RequestItem.created_at, RequestItem.item_id)
        )
        for rid, iid in rows:
            grouped[rid].append(iid)
        return grouped

    async def request_ids_for_items(self, item_ids: Iterable[str]) -> dict[str, list[uuid.UUID]]:
        ids = list(dict.fromkeys(item_ids))
        grouped: dict[str, list[uuid.UUID]] = {iid: [] for iid in ids}
        if not ids:
            return grouped
        rows = await self._session.execute(
            select(RequestItem.item_id, RequestItem.request_id)
            .where(RequestItem.item_id.in_(ids))
            .order_by(RequestItem.created_at)
        )
        for iid, rid in rows:
            grouped[iid].append(rid)
        return grouped

    async def mark_expanded(
        self, request_id: uuid.UUID, item_count: int, *, at: datetime | None = None
    ) -> None:
        await self._session.execute(
            update(Request)
            .where(Request.id == request_id)
            .values(
                status=RequestStatus.expanded.value,
                item_count=item_count,
                error=None,
                expanded_at=at or utcnow(),
            )
        )
        await self._refresh_if_loaded(request_id)

    async def mark_failed(self, request_id: uuid.UUID, error: str) -> None:
        await self._session.execute(
            update(Request)
            .where(Request.id == request_id)
            .values(status=RequestStatus.failed.value, error=error)
        )
        await self._refresh_if_loaded(request_id)

    async def _refresh_if_loaded(self, request_id: uuid.UUID) -> None:
        instance = self._session.identity_map.get((Request, (request_id,), None))
        if instance is not None:
            await self._session.refresh(instance)


__all__ = ["RequestRepository"]
