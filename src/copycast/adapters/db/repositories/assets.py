"""Assets: Artwork, chapters and transcripts keyed by their identity tuple."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.base import rows_affected
from copycast.adapters.db.models import Asset
from copycast.domain.enums import AssetFormat, AssetKind, AssetProvenance, AssetState
from copycast.domain.ids import asset_id as make_asset_id


class AssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, asset_id: str) -> Asset | None:
        return await self._session.get(Asset, asset_id)

    async def for_feed(self, feed_id: str) -> list[Asset]:
        result = await self._session.execute(
            select(Asset)
            .where(Asset.feed_id == feed_id)
            .order_by(Asset.item_id.asc().nulls_first(), Asset.kind, Asset.language, Asset.format)
        )
        return list(result.scalars())

    async def for_item(self, item_id: str) -> list[Asset]:
        result = await self._session.execute(
            select(Asset)
            .where(Asset.item_id == item_id)
            .order_by(Asset.kind, Asset.language, Asset.format, Asset.provenance)
        )
        return list(result.scalars())

    async def for_items(self, item_ids: Iterable[str]) -> dict[str, list[Asset]]:
        ids = list(dict.fromkeys(item_ids))
        grouped: dict[str, list[Asset]] = {iid: [] for iid in ids}
        if not ids:
            return grouped
        result = await self._session.execute(
            select(Asset)
            .where(Asset.item_id.in_(ids))
            .order_by(Asset.item_id, Asset.kind, Asset.language, Asset.format, Asset.provenance)
        )
        for asset in result.scalars():
            if asset.item_id is not None:
                grouped[asset.item_id].append(asset)
        return grouped

    async def feed_artwork(self, feed_id: str) -> Asset | None:
        result = await self._session.execute(
            select(Asset).where(
                Asset.feed_id == feed_id,
                Asset.item_id.is_(None),
                Asset.kind == AssetKind.artwork.value,
            )
        )
        return result.scalar_one_or_none()

    async def by_basename(self, feed_id: str, basename: str) -> Asset | None:
        """The archived asset whose ``local_path`` ends in ``basename`` (public asset route)."""
        result = await self._session.execute(
            select(Asset).where(
                Asset.feed_id == feed_id,
                Asset.state == AssetState.archived.value,
                Asset.local_path.is_not(None),
                func.regexp_replace(Asset.local_path, "^.*/", "") == basename,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        feed_id: str,
        item_id: str | None,
        kind: AssetKind,
        *,
        provenance: AssetProvenance = AssetProvenance.mirrored,
        language: str | None = None,
        format: AssetFormat | None = None,
        remote_url: str | None = None,
        local_path: str | None = None,
        mime: str | None = None,
        size_bytes: int | None = None,
        state: AssetState = AssetState.wanted,
        last_error: str | None = None,
        fetched_at: datetime | None = None,
        asset_id: str | None = None,
        slot: str | None = None,
    ) -> Asset:
        """Insert or update the asset with this identity; the id is derived from the identity."""
        aid = asset_id or make_asset_id(
            feed_id,
            item_id,
            kind.value,
            language,
            format.value if format else None,
            provenance.value,
            slot,
        )
        values: dict[str, Any] = {
            "id": aid,
            "feed_id": feed_id,
            "item_id": item_id,
            "slot": slot,
            "kind": kind.value,
            "provenance": provenance.value,
            "language": language,
            "format": format.value if format else None,
            "remote_url": remote_url,
            "local_path": local_path,
            "mime": mime,
            "size_bytes": size_bytes,
            "state": state.value,
            "last_error": last_error,
            "fetched_at": fetched_at,
        }
        stmt = insert(Asset).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Asset.id],
            set_={
                k: v
                for k, v in values.items()
                if k not in ("id", "feed_id", "item_id", "kind", "provenance", "language", "format")
            },
        )
        await self._session.execute(stmt)
        asset = await self._session.get(Asset, aid, populate_existing=True)
        if asset is None:  # pragma: no cover - the upsert just wrote it
            raise RuntimeError(f"asset {aid} vanished after upsert")
        return asset

    async def mark(
        self,
        asset_id: str,
        state: AssetState,
        *,
        local_path: str | None = None,
        mime: str | None = None,
        size_bytes: int | None = None,
        last_error: str | None = None,
        fetched_at: datetime | None = None,
    ) -> bool:
        values: dict[str, Any] = {"state": state.value, "last_error": last_error}
        if local_path is not None:
            values["local_path"] = local_path
        if mime is not None:
            values["mime"] = mime
        if size_bytes is not None:
            values["size_bytes"] = size_bytes
        if fetched_at is not None:
            values["fetched_at"] = fetched_at
        result = await self._session.execute(
            update(Asset).where(Asset.id == asset_id).values(**values).returning(Asset.id)
        )
        changed = result.scalar_one_or_none() is not None
        instance = self._session.identity_map.get((Asset, (asset_id,), None))
        if instance is not None:
            await self._session.refresh(instance)
        return changed

    async def delete(self, asset_id: str) -> bool:
        result = await self._session.execute(delete(Asset).where(Asset.id == asset_id))
        return rows_affected(result) > 0

    async def delete_for_item(self, item_id: str) -> list[Asset]:
        """Remove every asset row of an item; returns the rows so files can be unlinked."""
        rows = await self.for_item(item_id)
        if rows:
            await self._session.execute(delete(Asset).where(Asset.item_id == item_id))
            for row in rows:
                self._session.expunge(row)
        return rows

    async def archived_bytes(self, feed_id: str) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.sum(Asset.size_bytes), 0)).where(
                Asset.feed_id == feed_id, Asset.state == AssetState.archived.value
            )
        )
        return int(result.scalar_one() or 0)


__all__ = ["AssetRepository"]
