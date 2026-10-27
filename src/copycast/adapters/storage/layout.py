"""The Object store layout (version 2): the data directory's names, as object keys.

::

    LAYOUT_VERSION                                  "2\\n"
    engine/cookies.txt                              the Engine's cookie jar (optional)
    engine/cookies/{name}.txt                       the stored files the jar merges
    engine/defaults.json                            operator defaults every Mirror may override
    feeds/{feed_id}/feed.json                       descriptor
      source/feed.xml | source/listing.json         verbatim Source XML | last flat listing
      media/{item_id}.{ext}                         audio, {item_id}.info.json, {item_id}.item.xml
      assets/feed.artwork.{ext}  assets/{item_id}.artwork.{ext} | {item_id}.chapters.json
             | {item_id}.transcript.{lang}.{provenance}.{ext}
             | {item_id}.attachment.{slot}.{ext}
    uploads/{upload_id}/meta.json | file            files PUT to an upload URL, staged until a
                                                    Bucket takes them (ADR 0015, ADR 0018)

Layout 1 was the same tree on a local disk (plus ``tmp/``, which has become the
worker's Scratch); ``copycast storage push`` copies a layout-1 directory into
the Object store. Every key helper is pure; the operations go through :attr:`store`.
Relative paths stored in Postgres (``media_path``, ``local_path``) are the key
minus ``feeds/{feed_id}/``, exactly as they were relative to the feed directory.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

from copycast.adapters.storage.objects import ObjectStore
from copycast.application.ports import StoredObject
from copycast.domain.enums import AssetKind, AssetProvenance
from copycast.domain.ids import FEED_ID_RE
from copycast.settings import SettingsError

LAYOUT_VERSION: Final = "2"
LEGACY_LAYOUT_VERSION: Final = "1"
LAYOUT_VERSION_FILE: Final = "LAYOUT_VERSION"
FEEDS_DIR: Final = "feeds"
UPLOADS_DIR: Final = "uploads"
ENGINE_DIR: Final = "engine"
COOKIES_NAME: Final = "cookies.txt"
COOKIE_FILES_DIR: Final = "cookies"
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
_SLOT_RE: Final = re.compile(r"^[0-9a-f]{12}$")
_ATTACHMENT_RE: Final = re.compile(
    r"^(?P<item_id>[0-9a-f]{16})\.attachment\.(?P<slot>[0-9a-f]{12})\.(?P<ext>[A-Za-z0-9]{1,8})$"
)
_SIDECAR_SUFFIXES: Final = (INFO_JSON_SUFFIX, ITEM_XML_SUFFIX, PART_SUFFIX, ".ytdl", ".tmp")


class LayoutMismatch(SettingsError):
    """``LAYOUT_VERSION`` in the Object store is not the version this build understands."""


class UnsafePath(ValueError):
    """A relative path or basename would escape its feed prefix."""


@dataclass(frozen=True, slots=True)
class AssetFileName:
    """The parsed name of one object under ``assets/``."""

    kind: AssetKind
    item_id: str | None
    ext: str
    language: str | None = None
    provenance: AssetProvenance = AssetProvenance.mirrored
    slot: str | None = None


class Layout:
    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ root

    version_key: Final = LAYOUT_VERSION_FILE

    def cookies_key(self) -> str:
        """``engine/cookies.txt``: handed to yt-dlp as ``cookiefile`` (a Scratch copy)."""
        return f"{ENGINE_DIR}/{COOKIES_NAME}"

    def cookie_files_prefix(self) -> str:
        """``engine/cookies/``: one Netscape file per upload, merged into the jar."""
        return f"{ENGINE_DIR}/{COOKIE_FILES_DIR}/"

    def cookie_file_key(self, name: str) -> str:
        return self.cookie_files_prefix() + self.safe_basename(name)

    def uploads_prefix(self) -> str:
        """``uploads/``: files PUT to an upload URL, staged until a Bucket takes them."""
        return f"{UPLOADS_DIR}/"

    def defaults_key(self) -> str:
        """``engine/defaults.json``: the operator's defaults (language, minimum length)."""
        return f"{ENGINE_DIR}/{DEFAULTS_NAME}"

    def read_version(self) -> str | None:
        text = self.store.get_text(self.version_key)
        return text.strip() or None if text is not None else None

    def ensure_version(self) -> str:
        """Write ``LAYOUT_VERSION`` only when absent and no feed exists; refuse a mismatch."""
        self.store.ping()
        current = self.read_version()
        if current == LAYOUT_VERSION:
            return current
        if current is not None:
            raise LayoutMismatch(
                f"storage {self.store.url} has layout version {current!r}; this build needs "
                f"{LAYOUT_VERSION!r} (finish `copycast storage push`, or use an empty bucket)"
            )
        if self.feed_ids():
            raise LayoutMismatch(
                f"storage {self.store.url} contains feeds but no {LAYOUT_VERSION_FILE}; "
                "refusing to guess its layout"
            )
        self.store.put_bytes(self.version_key, f"{LAYOUT_VERSION}\n")
        return LAYOUT_VERSION

    # ------------------------------------------------------------------ per-feed keys

    def feed_prefix(self, feed_id: str) -> str:
        """``feeds/{feed_id}/`` (with the slash), the prefix of everything a Feed keeps."""
        if not FEED_ID_RE.match(feed_id):
            raise UnsafePath(f"invalid feed id {feed_id!r}")
        return f"{FEEDS_DIR}/{feed_id}/"

    def descriptor_key(self, feed_id: str) -> str:
        return self.feed_prefix(feed_id) + DESCRIPTOR_NAME

    def source_xml_key(self, feed_id: str) -> str:
        return self.feed_prefix(feed_id) + f"source/{SOURCE_XML_NAME}"

    def source_listing_key(self, feed_id: str) -> str:
        return self.feed_prefix(feed_id) + f"source/{SOURCE_LISTING_NAME}"

    def media_prefix(self, feed_id: str) -> str:
        return self.feed_prefix(feed_id) + "media/"

    def assets_prefix(self, feed_id: str) -> str:
        return self.feed_prefix(feed_id) + "assets/"

    def media_key(self, feed_id: str, item_id: str, ext: str) -> str:
        return self.media_prefix(feed_id) + f"{_item(item_id)}.{_ext(ext)}"

    def info_json_key(self, feed_id: str, item_id: str) -> str:
        return self.media_prefix(feed_id) + f"{_item(item_id)}{INFO_JSON_SUFFIX}"

    def item_xml_key(self, feed_id: str, item_id: str) -> str:
        return self.media_prefix(feed_id) + f"{_item(item_id)}{ITEM_XML_SUFFIX}"

    def asset_key(self, feed_id: str, basename: str) -> str:
        """The key of an asset object from its basename (``mirror.asset_basename``)."""
        return self.assets_prefix(feed_id) + self.safe_basename(basename)

    def feed_artwork_key(self, feed_id: str, ext: str) -> str:
        return self.assets_prefix(feed_id) + f"{FEED_ARTWORK_STEM}.{_ext(ext)}"

    def item_artwork_key(self, feed_id: str, item_id: str, ext: str) -> str:
        return self.assets_prefix(feed_id) + f"{_item(item_id)}.artwork.{_ext(ext)}"

    def chapters_key(self, feed_id: str, item_id: str) -> str:
        return self.assets_prefix(feed_id) + f"{_item(item_id)}.chapters.json"

    def attachment_key(self, feed_id: str, item_id: str, slot: str, ext: str) -> str:
        if not _SLOT_RE.match(slot):
            raise UnsafePath(f"invalid attachment slot {slot!r}")
        return self.assets_prefix(feed_id) + f"{_item(item_id)}.attachment.{slot}.{_ext(ext)}"

    def transcript_key(
        self, feed_id: str, item_id: str, language: str, provenance: AssetProvenance | str, ext: str
    ) -> str:
        lang = language.strip()
        if not re.match(r"^[A-Za-z0-9-]{1,16}$", lang):
            raise UnsafePath(f"invalid transcript language {language!r}")
        prov = AssetProvenance(provenance).value
        return self.assets_prefix(feed_id) + (
            f"{_item(item_id)}.transcript.{lang}.{prov}.{_ext(ext)}"
        )

    # ------------------------------------------------------------------ relative paths

    def relative(self, feed_id: str, key: str) -> str:
        """``key`` relative to the feed prefix (what the DB stores)."""
        prefix = self.feed_prefix(feed_id)
        if not key.startswith(prefix) or ".." in PurePosixPath(key).parts:
            raise UnsafePath(f"{key} is outside feed {feed_id}")
        return key.removeprefix(prefix)

    def resolve(self, feed_id: str, relative: str) -> str:
        """A stored relative path back to its key, refusing traversal."""
        rel = PurePosixPath(relative)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
            raise UnsafePath(f"unsafe relative path {relative!r}")
        return self.feed_prefix(feed_id) + rel.as_posix()

    @staticmethod
    def safe_basename(name: str) -> str:
        if not name or "/" in name or "\\" in name or name.startswith(".") or name in {"..", "."}:
            raise UnsafePath(f"unsafe file name {name!r}")
        return name

    # ------------------------------------------------------------------ object operations

    def read_json(self, key: str) -> Any | None:
        """The parsed JSON object at ``key``; ``None`` when absent or unreadable."""
        body = self.store.get_bytes(key)
        if body is None:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    def write_json(self, key: str, payload: Any, *, indent: int | None = 2) -> int:
        text = json.dumps(payload, indent=indent, ensure_ascii=False, default=str) + "\n"
        return self.store.put_bytes(key, text, content_type="application/json")

    def feed_ids(self) -> list[str]:
        """Feed prefixes that look like feed ids and still hold an object, sorted."""
        return [
            name
            for name in self.store.list_dirs(f"{FEEDS_DIR}/")
            if FEED_ID_RE.match(name) and self.store.has_any(f"{FEEDS_DIR}/{name}/")
        ]

    def has_feed(self, feed_id: str) -> bool:
        return self.store.has_any(self.feed_prefix(feed_id))

    def find_media(self, feed_id: str, item_id: str) -> StoredObject | None:
        """The audio object for ``item_id`` (any extension), ignoring sidecars."""
        stem = _item(item_id)
        for obj in self.store.list(self.media_prefix(feed_id)):
            if obj.name.startswith(f"{stem}.") and is_media_name(obj.name):
                return obj
        return None

    def media_objects(self, feed_id: str) -> list[StoredObject]:
        return [o for o in self.store.list(self.media_prefix(feed_id)) if is_media_name(o.name)]

    def asset_objects(self, feed_id: str) -> list[StoredObject]:
        return [
            o
            for o in self.store.list(self.assets_prefix(feed_id))
            if not o.name.startswith(".") and not o.name.endswith((".tmp", PART_SUFFIX))
        ]

    def remove_feed(self, feed_id: str) -> bool:
        """Delete every object of the feed (media, assets, sidecars, descriptor)."""
        return self.store.delete_prefix(self.feed_prefix(feed_id)) > 0

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
        if m := _ATTACHMENT_RE.match(name):
            return AssetFileName(
                AssetKind.attachment, m.group("item_id"), m.group("ext"), slot=m.group("slot")
            )
        return None

    @staticmethod
    def media_stem(name: str) -> str | None:
        """The item id of a media object name, or ``None`` for sidecars and foreign objects."""
        if not is_media_name(name):
            return None
        stem, _, _ = name.partition(".")
        return stem if ITEM_ID_RE.match(stem) else None


def is_media_name(name: str) -> bool:
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
    "LEGACY_LAYOUT_VERSION",
    "PART_SUFFIX",
    "AssetFileName",
    "Layout",
    "LayoutMismatch",
    "UnsafePath",
    "is_media_name",
]
