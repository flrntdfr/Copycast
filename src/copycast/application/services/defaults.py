"""Operator defaults every Mirror may override: language and minimum item length.

They live in ``engine/defaults.json`` under the data directory so a rebuild
keeps them, and both processes read them: the API to show and edit, the
worker to fill in what a Mirror leaves null (:func:`effective`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from copycast.application.capabilities import capability
from copycast.application.models import MirrorDefaults
from copycast.application.services.context import LayoutPort, ServiceContext
from copycast.logging import get_logger

log = get_logger(__name__)


def load_defaults(layout: LayoutPort) -> MirrorDefaults:
    """The stored defaults, or the built-in ones when the file is absent or unreadable."""
    path = layout.defaults_path()
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return MirrorDefaults()
    try:
        return MirrorDefaults.model_validate(data)
    except ValidationError:
        log.warning("defaults.unreadable", path=str(path))
        return MirrorDefaults()


def store_defaults(path: Path, defaults: MirrorDefaults) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(defaults.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def effective(
    defaults: MirrorDefaults, *, language: str | None, min_duration_seconds: int | None
) -> MirrorDefaults:
    """A Mirror's own values with the defaults filling in whatever is null."""
    return MirrorDefaults(
        language=language or defaults.language,
        min_duration_seconds=(
            min_duration_seconds
            if min_duration_seconds is not None
            else defaults.min_duration_seconds
        ),
    )


@capability("get_mirror_defaults", response=MirrorDefaults)
async def get_mirror_defaults(ctx: ServiceContext) -> MirrorDefaults:
    """The operator defaults applied wherever a Mirror leaves a value null."""
    return await asyncio.to_thread(load_defaults, ctx.layout)


@capability("set_mirror_defaults", request=MirrorDefaults, response=MirrorDefaults)
async def set_mirror_defaults(ctx: ServiceContext, body: MirrorDefaults) -> MirrorDefaults:
    """Replace the operator defaults; Mirrors with their own value are unaffected."""
    await asyncio.to_thread(store_defaults, ctx.layout.defaults_path(), body)
    log.info(
        "defaults.stored", language=body.language, min_duration_seconds=body.min_duration_seconds
    )
    return body


__all__ = [
    "effective",
    "get_mirror_defaults",
    "load_defaults",
    "set_mirror_defaults",
    "store_defaults",
]
