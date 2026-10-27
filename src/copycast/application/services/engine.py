"""The Engine's cookie files: stored for every fetch, never shown back.

Sites such as YouTube refuse downloads from a server's address unless a
logged-in session's cookies come along; the operator exports them from a
browser and adds them here, one file per site or session. Each file lives at
``data/engine/cookies/{name}.txt`` (mode 0600); the Engine reads one jar,
``data/engine/cookies.txt``, merged from them on every change. yt-dlp writes rotated
cookies into that jar, so every change first carries the jar back into the files.
A jar stored before files existed shows as the file ``cookies``.
"""

from __future__ import annotations

import asyncio
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

from copycast.application.capabilities import capability
from copycast.application.models import (
    CookieServiceRead,
    EngineCookieFileRead,
    EngineCookiesRead,
    EngineCookiesWrite,
)
from copycast.application.services.context import ServiceContext
from copycast.domain.cookies import (
    COOKIE_SERVICES,
    YOUTUBE_DOMAINS,
    cookie_file_name,
    cookie_lines,
    merge_cookie_files,
    parse_cookies,
    services_for,
    split_cookie_jar,
)
from copycast.domain.exceptions import NotFound
from copycast.logging import get_logger

log = get_logger(__name__)

COOKIES_MODE = stat.S_IRUSR | stat.S_IWUSR
LEGACY_NAME = "cookies"


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


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


class _Store:
    """The jar the Engine reads and the files it is merged from."""

    def __init__(self, jar: Path, folder: Path) -> None:
        self.jar = jar
        self.folder = folder

    def paths(self) -> dict[str, Path]:
        """Every stored file by name; a jar stored before files existed is ``cookies``."""
        if not self.folder.is_dir():
            return {LEGACY_NAME: self.jar} if self.jar.is_file() else {}
        return {path.stem: path for path in sorted(self.folder.glob("*.txt"))}

    def load(self) -> dict[str, str]:
        files = {name: _text(path) for name, path in self.paths().items()}
        return {name: text for name, text in files.items() if text is not None}

    def synced(self) -> dict[str, str]:
        """The stored files with what the Engine changed in the jar carried back."""
        files = self.load()
        jar = _text(self.jar) if files else None
        return split_cookie_jar(jar, files) if jar is not None else files

    def save(self, files: dict[str, str]) -> None:
        """Write the files that changed, drop the others, then merge the jar again."""
        self.folder.mkdir(parents=True, exist_ok=True)
        for path in self.folder.glob("*.txt"):
            if path.stem not in files:
                path.unlink(missing_ok=True)
        for name, text in files.items():
            path = self.folder / f"{name}.txt"
            stored = _text(path)
            if stored is None or cookie_lines(stored) != cookie_lines(text):
                _write(path, text)
        if files:
            _write(self.jar, merge_cookie_files(files))
        else:
            self.jar.unlink(missing_ok=True)

    def read(self) -> EngineCookiesRead:
        files = [_file_read(name, path) for name, path in self.paths().items()]
        files = [file for file in files if file is not None]
        if not files:
            return EngineCookiesRead(present=False, services=_services([]))
        domains = sorted({domain for file in files for domain in file.domains})
        stamps = [file.updated_at for file in files if file.updated_at is not None]
        return EngineCookiesRead(
            present=True,
            size_bytes=sum(file.size_bytes for file in files),
            updated_at=max(stamps, default=None),
            cookie_count=sum(file.cookie_count for file in files),
            domains=domains,
            youtube=any(domain in YOUTUBE_DOMAINS for domain in domains),
            files=files,
            services=_services(files),
        )


def _file_read(name: str, path: Path) -> EngineCookieFileRead | None:
    text = _text(path)
    try:
        info = path.stat()
    except OSError:
        return None
    if text is None:
        return None
    updated_at = datetime.fromtimestamp(info.st_mtime, tz=UTC)
    try:
        summary = parse_cookies(text)
    except Exception:  # an operator-edited file that no longer parses is still listed
        return EngineCookieFileRead(name=name, size_bytes=info.st_size, updated_at=updated_at)
    return EngineCookieFileRead(
        name=name,
        size_bytes=info.st_size,
        updated_at=updated_at,
        cookie_count=summary.cookie_count,
        domains=summary.domains,
        services=services_for(summary.domains),
    )


def _services(files: list[EngineCookieFileRead]) -> list[CookieServiceRead]:
    rows: list[CookieServiceRead] = []
    for service in COOKIE_SERVICES:
        unlocking = [file.name for file in files if service.id in file.services]
        rows.append(
            CookieServiceRead(
                id=service.id,
                name=service.name,
                domains=sorted(service.domains),
                authenticated=bool(unlocking),
                files=unlocking,
            )
        )
    return rows


def _store(ctx: ServiceContext) -> _Store:
    return _Store(ctx.layout.cookies_path(), ctx.layout.cookie_files_dir())


@capability("get_engine_cookies", response=EngineCookiesRead)
async def get_engine_cookies(ctx: ServiceContext) -> EngineCookiesRead:
    """The stored cookie files, which domains they cover and which services they unlock."""
    return await asyncio.to_thread(_store(ctx).read)


@capability("set_engine_cookies", request=EngineCookiesWrite, response=EngineCookiesRead)
async def set_engine_cookies(ctx: ServiceContext, body: EngineCookiesWrite) -> EngineCookiesRead:
    """Store a Netscape cookie file under its name, replacing a file of the same name
    (422 when it is not one); every future fetch carries it."""
    summary = parse_cookies(body.content)
    name = cookie_file_name(body.name)
    store = _store(ctx)

    def _add() -> EngineCookiesRead:
        files = store.synced()
        files[name] = body.content
        store.save(files)
        return store.read()

    read = await asyncio.to_thread(_add)
    log.info(
        "engine.cookies_stored", file=name, cookies=summary.cookie_count, domains=summary.domains
    )
    return read


@capability("delete_engine_cookies")
async def delete_engine_cookies(ctx: ServiceContext, name: str | None = None) -> None:
    """Remove one stored cookie file, or every one without ``name``."""
    store = _store(ctx)

    def _remove() -> None:
        files = store.synced()
        if name is not None and name not in files:
            raise NotFound("cookie file", name)
        store.save({} if name is None else {k: v for k, v in files.items() if k != name})

    await asyncio.to_thread(_remove)
    log.info("engine.cookies_removed", file=name)


__all__ = ["COOKIES_MODE", "delete_engine_cookies", "get_engine_cookies", "set_engine_cookies"]
