"""API keys: the MCP credentials an operator mints from the UI and revokes there.

A key is shown once at creation and stored as a SHA-256 digest; its scope
(read, write or full) is what the MCP tools check on every call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from copycast.application.capabilities import capability
from copycast.application.models import ApiKeyCreate, ApiKeyCreated, ApiKeyList, ApiKeyRead
from copycast.application.services.context import ServiceContext
from copycast.application.services.readmodels import api_key_read
from copycast.domain.credentials import (
    api_key_hash,
    api_key_prefix,
    looks_like_api_key,
    new_api_key,
)
from copycast.domain.exceptions import NotFound
from copycast.logging import get_logger

log = get_logger(__name__)

LAST_USED_GRANULARITY = timedelta(minutes=1)
"""``last_used_at`` is written at most this often per key; a busy agent must not write per call."""


@capability("list_api_keys", response=ApiKeyList)
async def list_api_keys(ctx: ServiceContext) -> ApiKeyList:
    """Every API key, newest first, without its secret."""
    async with ctx.uow_factory() as uow:
        rows = await uow.api_keys.list()
        return ApiKeyList(keys=[api_key_read(row) for row in rows])


@capability("create_api_key", request=ApiKeyCreate, response=ApiKeyCreated)
async def create_api_key(ctx: ServiceContext, body: ApiKeyCreate) -> ApiKeyCreated:
    """Mint a key; the secret in the response is the only time it is ever shown."""
    secret = new_api_key()
    async with ctx.uow_factory() as uow:
        row = await uow.api_keys.add(
            name=body.name,
            scope=body.scope,
            key_hash=api_key_hash(secret),
            prefix=api_key_prefix(secret),
        )
        key = api_key_read(row)
    log.info("api_key.created", key_id=str(key.id), name=key.name, scope=key.scope.value)
    return ApiKeyCreated(key=key, secret=secret)


@capability("revoke_api_key")
async def revoke_api_key(ctx: ServiceContext, key_id: uuid.UUID) -> None:
    """Delete a key; the next MCP call with it is refused."""
    async with ctx.uow_factory() as uow:
        if not await uow.api_keys.delete(key_id):
            raise NotFound("api key", str(key_id))
    log.info("api_key.revoked", key_id=str(key_id))


@capability("authenticate_api_key", response=ApiKeyRead)
async def authenticate_api_key(ctx: ServiceContext, secret: str) -> ApiKeyRead | None:
    """The key a bearer secret belongs to, or ``None``; records the use (throttled)."""
    if not looks_like_api_key(secret):
        return None
    async with ctx.uow_factory() as uow:
        row = await uow.api_keys.by_hash(api_key_hash(secret))
        if row is None:
            return None
        now = datetime.now(UTC)
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_GRANULARITY:
            await uow.api_keys.touch(row.id, now)
        return api_key_read(row)


__all__ = [
    "LAST_USED_GRANULARITY",
    "authenticate_api_key",
    "create_api_key",
    "list_api_keys",
    "revoke_api_key",
]
