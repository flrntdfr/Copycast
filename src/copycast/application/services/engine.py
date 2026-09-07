"""The Engine's cookie file: stored once for every fetch, never shown back.

Sites such as YouTube refuse downloads from a server's address unless a
logged-in session's cookies come along; the operator exports them from a
browser and pastes them here. The file lives at ``data/engine/cookies.txt``
(mode 0600) and the engine adapter hands it to yt-dlp as ``cookiefile``.
"""

from __future__ import annotations

import asyncio
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from copycast.application.capabilities import capability
from copycast.application.models import EngineCookiesRead, EngineCookiesWrite
from copycast.application.services.context import ServiceContext
from copycast.domain.cookies import parse_cookies
from copycast.logging import get_logger

log = get_logger(__name__)

COOKIES_MODE = stat.S_IRUSR | stat.S_IWUSR


def _read(path: Path) -> EngineCookiesRead:
    try:
        text = path.read_text(encoding="utf-8")
        info = path.stat()
    except (OSError, UnicodeDecodeError):
        return EngineCookiesRead(present=False)
    try:
        summary = parse_cookies(text)
    except Exception:  # an operator-edited file that no longer parses is still "present"
        return EngineCookiesRead(
            present=True,
            size_bytes=info.st_size,
            updated_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
        )
    return EngineCookiesRead(
        present=True,
        size_bytes=info.st_size,
        updated_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
        cookie_count=summary.cookie_count,
        domains=summary.domains,
        youtube=summary.youtube,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, COOKIES_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
    os.chmod(tmp, COOKIES_MODE)
    os.replace(tmp, path)


@capability("get_engine_cookies", response=EngineCookiesRead)
async def get_engine_cookies(ctx: ServiceContext) -> EngineCookiesRead:
    """Whether a cookie file is stored, how big it is and which domains it covers."""
    return await asyncio.to_thread(_read, ctx.layout.cookies_path())


@capability("set_engine_cookies", request=EngineCookiesWrite, response=EngineCookiesRead)
async def set_engine_cookies(ctx: ServiceContext, body: EngineCookiesWrite) -> EngineCookiesRead:
    """Store a Netscape cookie file for every future fetch (422 when it is not one)."""
    summary = parse_cookies(body.content)
    path = ctx.layout.cookies_path()
    await asyncio.to_thread(_write, path, body.content)
    log.info("engine.cookies_stored", cookies=summary.cookie_count, domains=summary.domains)
    return await asyncio.to_thread(_read, path)


@capability("delete_engine_cookies")
async def delete_engine_cookies(ctx: ServiceContext) -> None:
    """Remove the stored cookie file; fetches go back to anonymous."""
    path = ctx.layout.cookies_path()

    def _remove() -> None:
        path.unlink(missing_ok=True)

    await asyncio.to_thread(_remove)
    log.info("engine.cookies_removed")


__all__ = ["COOKIES_MODE", "delete_engine_cookies", "get_engine_cookies", "set_engine_cookies"]
