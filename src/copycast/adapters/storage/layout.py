"""The data directory layout.

::

    {data_dir}/LAYOUT_VERSION                       "1\\n"
    {data_dir}/engine/cookies.txt                   the Engine's cookie file (optional, 0600)
    {data_dir}/engine/defaults.json                 operator defaults every Mirror may override
    {data_dir}/feeds/{feed_id}/feed.json            descriptor
      source/feed.xml | source/listing.json         verbatim Source XML | last flat listing
      media/{item_id}.{ext}                         audio, {item_id}.info.json, {item_id}.item.xml
      tmp/                                          yt-dlp temp; ignored by rebuild and recount
      assets/feed.artwork.{ext}  assets/{item_id}.artwork.{ext} | {item_id}.chapters.json
             | {item_id}.transcript.{lang}.{provenance}.{ext}

Every path helper is pure; only :meth:`Layout.ensure_version`,
:meth:`Layout.ensure_feed_dirs`, the ``remove_*`` helpers and the file
listings touch the disk.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from copycast.adapters.storage.atomic import is_temp_file, write_atomic
from copycast.domain.enums import AssetKind, AssetProvenance
from copycast.domain.ids import FEED_ID_RE
from copycast.settings import SettingsError

LAYOUT_VERSION: Final = "1"
LAYOUT_VERSION_FILE: Final = "LAYOUT_VERSION"
FEEDS_DIR: Final = "feeds"
ENGINE_DIR: Final = "engine"
COOKIES_NAME: Final = "cookies.txt"
DEFAULTS_NAME: Final = "defaults.json"
DESCRIPTOR_NAME: Final = "feed.json"
SOURCE_XML_NAME: Final = "feed.xml"
SOURCE_LISTING_NAME: Final = "listing.json"
FEED_ARTWORK_STEM: Final = "feed.artwork"
INFO_JSON_SUFFIX: Final = ".info.json"
ITEM_XML_SUFFIX: Final = ".item.xml"
PART_SUFFIX: Final = ".part"

ITEM_ID_RE: Final = re.compile(r"^[0-9a-f]{16}$")
_EXT_RE: Final = re.compile(r"^[A-Za-z0-9]{1,8}$")
_TRANSCRIPT_RE: Final = re.compile(
    r"^(?P<item_id>[0-9a-f]{16})\.transcript\.(?P<language>[A-Za-z0-9-]{1,16})"
    r"\.(?P<provenance>mirrored|generated)\.(?P<ext>[A-Za-z0-9]{1,8})$"
)
_ARTWORK_RE: Final = re.compile(r"^(?P<item_id>[0-9a-f]{16})\.artwork\.(?P<ext>[A-Za-z0-9]{1,8})$")
_FEED_ARTWORK_RE: Final = re.compile(r"^feed\.artwork\.(?P<ext>[A-Za-z0-9]{1,8})$")
_CHAPTERS_RE: Final = re.compile(r"^(?P<item_id>[0-9a-f]{16})\.chapters\.json$")
_SIDECAR_SUFFIXES: Final = (INFO_JSON_SUFFIX, ITEM_XML_SUFFIX, PART_SUFFIX, ".ytdl")


class LayoutMismatch(SettingsError):
    """``LAYOUT_VERSION`` on disk is not the version this build understands."""


class UnsafePath(ValueError):
    """A relative path or basename would escape its feed directory."""


@dataclass(frozen=True, slots=True)
class AssetFileName:
    """The parsed name of one file under ``assets/``."""

    kind: AssetKind
    item_id: str | None
    ext: str
    language: str | None = None
    provenance: AssetProvenance = AssetProvenance.mirrored


class Layout:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir)

    # ------------------------------------------------------------------ root

    @property
    def version_file(self) -> Path:
        return self.root / LAYOUT_VERSION_FILE

    @property
    def engine_dir(self) -> Path:
        return self.root / ENGINE_DIR

    def cookies_path(self) -> Path:
        """``engine/cookies.txt``: handed to yt-dlp as ``cookiefile`` when present."""
        return self.engine_dir / COOKIES_NAME

    def defaults_path(self) -> Path:
        """``engine/defaults.json``: the operator's defaults (language, minimum length)."""
        return self.engine_dir / DEFAULTS_NAME

    @property
    def feeds_dir(self) -> Path:
        return self.root / FEEDS_DIR

    def read_version(self) -> str | None:
        try:
            return self.version_file.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None

    def ensure_version(self) -> str:
        """Write ``LAYOUT_VERSION`` only when absent and no feed exists; refuse a mismatch."""
        current = self.read_version()
        if current == LAYOUT_VERSION:
            self.feeds_dir.mkdir(parents=True, exist_ok=True)
            return current
        if current is not None:
            raise LayoutMismatch(
                f"data directory {self.root} has layout version {current!r}; this build "
                f"needs {LAYOUT_VERSION!r} (use a matching Copycast release or a fresh directory)"
            )
        if self.feeds_dir.is_dir() and any(self.feeds_dir.iterdir()):
            raise LayoutMismatch(
                f"data directory {self.root} contains feeds but no {LAYOUT_VERSION_FILE} file; "
                "refusing to guess its layout"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.feeds_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self.version_file, f"{LAYOUT_VERSION}\n")
        return LAYOUT_VERSION

    # ------------------------------------------------------------------ per-feed paths

    def feed_dir(self, feed_id: str) -> Path:
        if not FEED_ID_RE.match(feed_id):
            raise UnsafePath(f"invalid feed id {feed_id!r}")
        return self.feeds_dir / feed_id

    def descriptor_path(self, feed_id: str) -> Path:
        return self.feed_dir(feed_id) / DESCRIPTOR_NAME

    def source_dir(self, feed_id: str) -> Path:
        return self.feed_dir(feed_id) / "source"

    def source_xml_path(self, feed_id: str) -> Path:
        return self.source_dir(feed_id) / SOURCE_XML_NAME

    def source_listing_path(self, feed_id: str) -> Path:
        return self.source_dir(feed_id) / SOURCE_LISTING_NAME

    def media_dir(self, feed_id: str) -> Path:
        return self.feed_dir(feed_id) / "media"

    def tmp_dir(self, feed_id: str) -> Path:
        return self.feed_dir(feed_id) / "tmp"

    def assets_dir(self, feed_id: str) -> Path:
        return self.feed_dir(feed_id) / "assets"

    def media_path(self, feed_id: str, item_id: str, ext: str) -> Path:
        return self.media_dir(feed_id) / f"{_item(item_id)}.{_ext(ext)}"

    def info_json_path(self, feed_id: str, item_id: str) -> Path:
        return self.media_dir(feed_id) / f"{_item(item_id)}{INFO_JSON_SUFFIX}"

    def item_xml_path(self, feed_id: str, item_id: str) -> Path:
        return self.media_dir(feed_id) / f"{_item(item_id)}{ITEM_XML_SUFFIX}"

    def feed_artwork_path(self, feed_id: str, ext: str) -> Path:
        return self.assets_dir(feed_id) / f"{FEED_ARTWORK_STEM}.{_ext(ext)}"

    def item_artwork_path(self, feed_id: str, item_id: str, ext: str) -> Path:
        return self.assets_dir(feed_id) / f"{_item(item_id)}.artwork.{_ext(ext)}"

    def chapters_path(self, feed_id: str, item_id: str) -> Path:
        return self.assets_dir(feed_id) / f"{_item(item_id)}.chapters.json"

    def transcript_path(
        self, feed_id: str, item_id: str, language: str, provenance: AssetProvenance | str, ext: str
    ) -> Path:
        lang = language.strip()
        if not re.match(r"^[A-Za-z0-9-]{1,16}$", lang):
            raise UnsafePath(f"invalid transcript language {language!r}")
        prov = AssetProvenance(provenance).value
        return self.assets_dir(feed_id) / f"{_item(item_id)}.transcript.{lang}.{prov}.{_ext(ext)}"

    def asset_path(
        self,
        feed_id: str,
        kind: AssetKind | str,
        item_id: str | None,
        ext: str,
        *,
        language: str | None = None,
        provenance: AssetProvenance | str = AssetProvenance.mirrored,
    ) -> Path:
        """The canonical file for an asset row's identity."""
        match AssetKind(kind):
            case AssetKind.artwork:
                if item_id is None:
                    return self.feed_artwork_path(feed_id, ext)
                return self.item_artwork_path(feed_id, item_id, ext)
            case AssetKind.chapters:
                if item_id is None:
                    raise UnsafePath("chapters belong to an item")
                return self.chapters_path(feed_id, item_id)
            case AssetKind.transcript:
                if item_id is None or not language:
                    raise UnsafePath("a transcript needs an item and a language")
                return self.transcript_path(feed_id, item_id, language, provenance, ext)

    # ------------------------------------------------------------------ relative paths

    def relative(self, feed_id: str, path: Path) -> str:
        """``path`` relative to the feed directory, POSIX style (what the DB stores)."""
        try:
            return path.resolve().relative_to(self.feed_dir(feed_id).resolve()).as_posix()
        except ValueError as exc:
            raise UnsafePath(f"{path} is outside feed {feed_id}") from exc

    def resolve(self, feed_id: str, relative: str) -> Path:
        """Join a stored relative path back onto the feed directory, refusing traversal."""
        rel = PurePosixPath(relative)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
            raise UnsafePath(f"unsafe relative path {relative!r}")
        return self.feed_dir(feed_id).joinpath(*rel.parts)

    @staticmethod
    def safe_basename(name: str) -> str:
        if not name or name != Path(name).name or name.startswith(".") or "/" in name:
            raise UnsafePath(f"unsafe file name {name!r}")
        return name

    # ------------------------------------------------------------------ disk operations

    def ensure_feed_dirs(self, feed_id: str) -> Path:
        base = self.feed_dir(feed_id)
        for sub in ("source", "media", "tmp", "assets"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        return base

    def feed_ids_on_disk(self) -> list[str]:
        """Feed directories that look like feed ids, sorted; unreadable names are skipped."""
        if not self.feeds_dir.is_dir():
            return []
        return sorted(
            p.name for p in self.feeds_dir.iterdir() if p.is_dir() and FEED_ID_RE.match(p.name)
        )

    def has_feed(self, feed_id: str) -> bool:
        return self.feed_dir(feed_id).is_dir()

    def find_media(self, feed_id: str, item_id: str) -> Path | None:
        """The audio file for ``item_id`` (any extension), ignoring sidecars and partials."""
        stem = _item(item_id)
        media = self.media_dir(feed_id)
        if not media.is_dir():
            return None
        for candidate in sorted(media.glob(f"{stem}.*")):
            if is_media_file(candidate):
                return candidate
        return None

    def media_files(self, feed_id: str) -> Iterator[Path]:
        media = self.media_dir(feed_id)
        if not media.is_dir():
            return
        for path in sorted(media.iterdir()):
            if path.is_file() and is_media_file(path):
                yield path

    def asset_files(self, feed_id: str) -> Iterator[Path]:
        assets = self.assets_dir(feed_id)
        if not assets.is_dir():
            return
        for path in sorted(assets.iterdir()):
            if path.is_file() and not is_temp_file(path) and not path.name.startswith("."):
                yield path

    def remove_tmp_leftovers(self, feed_id: str, item_id: str) -> int:
        """Delete ``tmp/{item_id}*`` (``.part`` and friends); returns the count removed."""
        stem = _item(item_id)
        tmp = self.tmp_dir(feed_id)
        if not tmp.is_dir():
            return 0
        removed = 0
        for path in tmp.glob(f"{stem}*"):
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def remove_feed(self, feed_id: str) -> bool:
        """Delete the whole feed directory (media, tmp, assets, descriptor)."""
        base = self.feed_dir(feed_id)
        if not base.exists():
            return False
        shutil.rmtree(base)
        return True

    # ------------------------------------------------------------------ name parsing

    @staticmethod
    def parse_asset_name(name: str) -> AssetFileName | None:
        """Interpret a basename under ``assets/``; ``None`` when it is not one of ours."""
        if m := _FEED_ARTWORK_RE.match(name):
            return AssetFileName(AssetKind.artwork, None, m.group("ext"))
        if m := _ARTWORK_RE.match(name):
            return AssetFileName(AssetKind.artwork, m.group("item_id"), m.group("ext"))
        if m := _CHAPTERS_RE.match(name):
            return AssetFileName(AssetKind.chapters, m.group("item_id"), "json")
        if m := _TRANSCRIPT_RE.match(name):
            return AssetFileName(
                AssetKind.transcript,
                m.group("item_id"),
                m.group("ext"),
                language=m.group("language"),
                provenance=AssetProvenance(m.group("provenance")),
            )
        return None

    @staticmethod
    def media_stem(path: Path) -> str | None:
        """The item id of a media file name, or ``None`` for sidecars and foreign files."""
        if not is_media_file(path):
            return None
        stem, _, _ = path.name.partition(".")
        return stem if ITEM_ID_RE.match(stem) else None


def is_media_file(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or any(name.endswith(suffix) for suffix in _SIDECAR_SUFFIXES):
        return False
    stem, dot, ext = name.partition(".")
    return bool(dot) and ITEM_ID_RE.match(stem) is not None and _EXT_RE.match(ext) is not None


def _item(item_id: str) -> str:
    if not ITEM_ID_RE.match(item_id):
        raise UnsafePath(f"invalid item id {item_id!r}")
    return item_id


def _ext(ext: str) -> str:
    ext = ext.lstrip(".")
    if not _EXT_RE.match(ext):
        raise UnsafePath(f"invalid file extension {ext!r}")
    return ext


__all__ = [
    "DESCRIPTOR_NAME",
    "INFO_JSON_SUFFIX",
    "ITEM_ID_RE",
    "ITEM_XML_SUFFIX",
    "LAYOUT_VERSION",
    "LAYOUT_VERSION_FILE",
    "PART_SUFFIX",
    "AssetFileName",
    "Layout",
    "LayoutMismatch",
    "UnsafePath",
    "is_media_file",
]
