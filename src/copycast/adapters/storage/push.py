"""``copycast storage push``: copy a layout-1 data directory into the Object store, once.

Every file under ``engine/`` and ``feeds/`` goes to the key of the same
relative name, except yt-dlp partials and the ``tmp/`` directories (Scratch
now). An object already present with the same size is skipped, so an
interrupted push resumes where it stopped; every upload is checked with a
``head`` of the same size. ``LAYOUT_VERSION`` 2 is written last, and only when
every file landed: until then the api and the worker refuse the Object store. The
data directory is left untouched (it is the rollback until the operator
deletes it).
"""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from copycast.adapters.storage.layout import (
    ENGINE_DIR,
    FEEDS_DIR,
    LAYOUT_VERSION,
    LAYOUT_VERSION_FILE,
    LEGACY_LAYOUT_VERSION,
    PART_SUFFIX,
    Layout,
    LayoutMismatch,
)
from copycast.logging import get_logger
from copycast.settings import SettingsError

log = get_logger(__name__)

SKIPPED_DIRS: Final = frozenset({"tmp"})
SKIPPED_SUFFIXES: Final = (PART_SUFFIX, ".ytdl", ".tmp")


class PushFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    error: str


class PushReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    bucket: str
    dry_run: bool
    files: int = 0
    uploaded: int = 0
    skipped_present: int = 0
    bytes_uploaded: int = 0
    failures: list[PushFailure] = Field(default_factory=list[PushFailure])
    layout_version_written: bool = False


def files_to_push(root: Path) -> Iterator[tuple[str, Path]]:
    """``(key, path)`` of every file worth keeping, sorted, descriptors last per feed.

    A feed's ``feed.json`` goes up after its media so a rebuild that runs on a
    half-pushed Object store never sees a descriptor whose files are still missing.
    """
    for top in (ENGINE_DIR, FEEDS_DIR):
        base = root / top
        if not base.is_dir():
            continue
        files = sorted(p for p in base.rglob("*") if p.is_file())
        files.sort(key=lambda p: (p.name == "feed.json", str(p)))
        for path in files:
            rel = path.relative_to(root)
            if any(part in SKIPPED_DIRS for part in rel.parts[:-1]):
                continue
            if path.name.startswith(".") or path.name.endswith(SKIPPED_SUFFIXES):
                continue
            yield rel.as_posix(), path


def push(layout: Layout, source: Path, *, dry_run: bool = False) -> PushReport:
    """Copy ``source`` (a layout-1 data directory) into ``layout.store``."""
    version_file = source / LAYOUT_VERSION_FILE
    try:
        found = version_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SettingsError(
            f"{source} is not a Copycast data directory (no {LAYOUT_VERSION_FILE})"
        ) from exc
    if found != LEGACY_LAYOUT_VERSION:
        raise LayoutMismatch(
            f"{source} has layout version {found!r}; storage push copies layout "
            f"{LEGACY_LAYOUT_VERSION!r} directories"
        )
    store = layout.store
    store.ping()
    current = layout.read_version()
    if current not in (None, LAYOUT_VERSION):
        raise LayoutMismatch(f"storage {store.url} has layout version {current!r}")
    files = uploaded = present = sent = 0
    failures: list[PushFailure] = []
    for key, path in files_to_push(source):
        files += 1
        size = path.stat().st_size
        existing = store.head(key)
        if existing is not None and existing.size == size:
            present += 1
            continue
        if dry_run:
            uploaded += 1
            sent += size
            continue
        try:
            store.put_file(key, path, content_type=mimetypes.guess_type(path.name)[0])
            landed = store.head(key)
            if landed is None or landed.size != size:
                raise OSError(f"size mismatch after upload ({landed.size if landed else 'absent'})")
        except Exception as exc:
            log.warning("storage.push_failed", key=key, error=str(exc))
            failures.append(PushFailure(key=key, error=str(exc)))
            continue
        uploaded += 1
        sent += size
    written = False
    if not dry_run and not failures and current is None:
        store.put_bytes(layout.version_key, f"{LAYOUT_VERSION}\n")
        written = True
    return PushReport(
        source=str(source),
        bucket=store.url,
        dry_run=dry_run,
        files=files,
        uploaded=uploaded,
        skipped_present=present,
        bytes_uploaded=sent,
        failures=failures,
        layout_version_written=written,
    )


__all__ = ["PushFailure", "PushReport", "files_to_push", "push"]
