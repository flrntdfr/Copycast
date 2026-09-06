"""About: application and engine versions, the layout version and the totals."""

from __future__ import annotations

import asyncio

from packaging.version import InvalidVersion, Version

from copycast.application.capabilities import capability
from copycast.application.models import AboutRead, EngineRead
from copycast.application.services.context import ServiceContext
from copycast.version import APP_VERSION

DEFAULT_LAYOUT_VERSION = "1"


def normalize_engine_version(text: str) -> str:
    """The PEP 440 form of an engine version (``2026.08.19`` -> ``2026.8.19``).

    yt-dlp reports zero-padded dates while the lockfile and the image tag
    ``1.0.0-yt<engine>`` carry the normalized form; every stored or displayed
    engine version uses the latter so they compare verbatim.
    """
    try:
        return str(Version(text.strip()))
    except InvalidVersion:
        return text.strip()


@capability("about", response=AboutRead)
async def about(ctx: ServiceContext) -> AboutRead:
    """Versions and totals for the About page and the MCP ``copycast://about`` resource."""
    engine = await asyncio.to_thread(ctx.engine.version)
    async with ctx.uow_factory() as uow:
        totals = await uow.feeds.totals()
    layout_version = ctx.layout.read_version() or DEFAULT_LAYOUT_VERSION
    return AboutRead(
        version=APP_VERSION,
        engine=EngineRead(
            name=engine.name,
            version=normalize_engine_version(engine.version),
            channel=engine.channel,
            release_date=engine.release_date,
            git_head=engine.git_head,
        ),
        ffmpeg_version=engine.ffmpeg_version,
        base_url=ctx.settings.base_url,
        layout_version=layout_version,
        totals=totals,
        auth_enabled=ctx.settings.auth.enabled,
    )


__all__ = ["DEFAULT_LAYOUT_VERSION", "about", "normalize_engine_version"]
