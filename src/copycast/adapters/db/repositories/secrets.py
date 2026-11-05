"""Instance secrets: minted once per database, read by every process."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copycast.adapters.db.models import InstanceSecret


class InstanceSecretRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, name: str, mint: Callable[[], bytes]) -> bytes:
        """The secret called ``name``, minted with ``mint`` when absent.

        ``INSERT ... ON CONFLICT DO NOTHING`` then ``SELECT``: two processes starting
        together both read the one row that won, never two different secrets.
        """
        await self._session.execute(
            insert(InstanceSecret)
            .values(name=name, value=mint())
            .on_conflict_do_nothing(index_elements=[InstanceSecret.name])
        )
        result = await self._session.execute(
            select(InstanceSecret.value).where(InstanceSecret.name == name)
        )
        return bytes(result.scalar_one())


__all__ = ["InstanceSecretRepository"]
