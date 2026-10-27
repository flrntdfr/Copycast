"""Operator defaults every Mirror may override: language, minimum item length, default policy.

They live in ``engine/defaults.json`` in the Object store so a rebuild keeps them,
and both processes read them: the API to show and edit, the worker to fill in
what a Mirror leaves null (:func:`effective`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ValidationError

from copycast.application.capabilities import capability
from copycast.application.models import MirrorDefaults
from copycast.application.services.context import LayoutPort, ServiceContext
from copycast.logging import get_logger

log = get_logger(__name__)


def load_defaults(layout: LayoutPort) -> MirrorDefaults:
    """The stored defaults, or the built-in ones when the object is absent or unreadable."""
    key = layout.defaults_key()
    try:
        data: Any = layout.read_json(key)
    except OSError:
        return MirrorDefaults()
    if data is None:
        return MirrorDefaults()
    try:
        return MirrorDefaults.model_validate(data)
    except ValidationError:
        log.warning("defaults.unreadable", key=key)
        return MirrorDefaults()


def store_defaults(layout: LayoutPort, defaults: MirrorDefaults) -> None:
    layout.store.put_bytes(
        layout.defaults_key(),
        defaults.model_dump_json(indent=2) + "\n",
        content_type="application/json",
    )


def effective(
    defaults: MirrorDefaults,
    *,
    language: str | None,
    min_duration_seconds: int | None,
    refresh_interval_hours: int | None = None,
) -> MirrorDefaults:
    """A Mirror's own values with the defaults filling in whatever is null."""
    return MirrorDefaults(
        language=language or defaults.language,
        min_duration_seconds=(
            min_duration_seconds
            if min_duration_seconds is not None
            else defaults.min_duration_seconds
        ),
        backfill=defaults.backfill,
        refresh_interval_hours=refresh_interval_hours or defaults.refresh_interval_hours,
    )


@capability("get_mirror_defaults", response=MirrorDefaults)
async def get_mirror_defaults(ctx: ServiceContext) -> MirrorDefaults:
    """The operator defaults applied wherever a Mirror leaves a value null."""
    return await asyncio.to_thread(load_defaults, ctx.layout)


@capability("set_mirror_defaults", request=MirrorDefaults, response=MirrorDefaults)
async def set_mirror_defaults(ctx: ServiceContext, body: MirrorDefaults) -> MirrorDefaults:
    """Replace the operator defaults; Mirrors with their own value are unaffected."""
    await asyncio.to_thread(store_defaults, ctx.layout, body)
    log.info(
        "defaults.stored",
        language=body.language,
        min_duration_seconds=body.min_duration_seconds,
        backfill_mode=body.backfill.mode.value,
        refresh_interval_hours=body.refresh_interval_hours,
    )
    return body


__all__ = [
    "effective",
    "get_mirror_defaults",
    "load_defaults",
    "set_mirror_defaults",
    "store_defaults",
]
