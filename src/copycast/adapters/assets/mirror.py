"""Mirror Source assets (Artwork, chapters, transcripts) into a feed's ``assets/`` directory.

Limits: Artwork 10 MiB and must sniff as an image; chapters 2 MiB and must
be JSON; transcripts 20 MiB in a known format. A failed asset raises
:class:`AssetError` so the caller marks the row ``failed`` while the archive
job itself still succeeds. Files are written atomically (``.part`` then
``os.replace``) under the plan's basenames.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from posixpath import basename as posix_basename
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import etree

from copycast.adapters.sources.http import BodyTooLarge, SourceError, fetch
from copycast.adapters.sources.rss import NS_ITUNES, NS_PODCAST, PARSER, tag
from copycast.domain.enums import AssetFormat, AssetKind, AssetProvenance

if TYPE_CHECKING:
    from lxml.etree import XmlElement

MiB: Final = 1024 * 1024
ARTWORK_MAX_BYTES: Final = 10 * MiB
CHAPTERS_MAX_BYTES: Final = 2 * MiB
TRANSCRIPT_MAX_BYTES: Final = 20 * MiB

LIMITS: Final[dict[AssetKind, int]] = {
    AssetKind.artwork: ARTWORK_MAX_BYTES,
    AssetKind.chapters: CHAPTERS_MAX_BYTES,
    AssetKind.transcript: TRANSCRIPT_MAX_BYTES,
}

FORMAT_MIME: Final[dict[AssetFormat, str]] = {
    AssetFormat.vtt: "text/vtt",
    AssetFormat.srt: "application/x-subrip",
    AssetFormat.json: "application/json",
    AssetFormat.text: "text/plain",
    AssetFormat.html: "text/html",
}

FORMAT_EXT: Final[dict[AssetFormat, str]] = {
    AssetFormat.vtt: "vtt",
    AssetFormat.srt: "srt",
    AssetFormat.json: "json",
    AssetFormat.text: "txt",
    AssetFormat.html: "html",
}

_MIME_FORMAT: Final[dict[str, AssetFormat]] = {
    "text/vtt": AssetFormat.vtt,
    "application/x-subrip": AssetFormat.srt,
    "application/srt": AssetFormat.srt,
    "text/srt": AssetFormat.srt,
    "application/json": AssetFormat.json,
    "application/json+chapters": AssetFormat.json,
    "text/json": AssetFormat.json,
    "text/plain": AssetFormat.text,
    "text/html": AssetFormat.html,
    "application/xhtml+xml": AssetFormat.html,
}

_EXT_FORMAT: Final[dict[str, AssetFormat]] = {
    "vtt": AssetFormat.vtt,
    "srt": AssetFormat.srt,
    "json": AssetFormat.json,
    "txt": AssetFormat.text,
    "text": AssetFormat.text,
    "html": AssetFormat.html,
    "htm": AssetFormat.html,
}

CHAPTERS_MIME: Final = "application/json+chapters"

_IMAGE_SIGNATURES: Final[tuple[tuple[bytes, str, str], ...]] = (
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


class AssetError(Exception):
    """The asset could not be mirrored; the message goes to ``assets.last_error``."""


@dataclass(frozen=True, slots=True)
class RemoteAsset:
    """An asset the Source advertises for a feed (``item_id`` None) or an item."""

    kind: AssetKind
    url: str
    item_id: str | None = None
    mime: str | None = None
    language: str | None = None
    format: AssetFormat | None = None
    provenance: AssetProvenance = AssetProvenance.mirrored


@dataclass(frozen=True, slots=True)
class MirroredAsset:
    """A mirrored file under ``assets/``; ``local_path`` is relative to the feed directory."""

    kind: AssetKind
    item_id: str | None
    language: str | None
    format: AssetFormat | None
    provenance: AssetProvenance
    basename: str
    path: Path
    mime: str
    size_bytes: int

    @property
    def local_path(self) -> str:
        return f"assets/{self.basename}"


def asset_basename(
    kind: AssetKind,
    *,
    ext: str,
    item_id: str | None = None,
    language: str | None = None,
    provenance: AssetProvenance = AssetProvenance.mirrored,
) -> str:
    """``feed.artwork.jpg`` | ``{item_id}.artwork.jpg`` | ``{item_id}.chapters.json`` |
    ``{item_id}.transcript.{lang}.{provenance}.{ext}``."""
    clean_ext = ext.lower().lstrip(".")
    stem = item_id or "feed"
    if kind is AssetKind.artwork:
        return f"{stem}.artwork.{clean_ext}"
    if kind is AssetKind.chapters:
        return f"{stem}.chapters.{clean_ext}"
    lang = language_tag(language)
    return f"{stem}.transcript.{lang}.{provenance.value}.{clean_ext}"


def language_tag(language: str | None) -> str:
    cleaned = "".join(ch for ch in (language or "").strip().lower() if ch.isalnum() or ch == "-")
    return cleaned or "und"


def sniff_image(head: bytes) -> tuple[str, str] | None:
    """``(ext, mime)`` when ``head`` starts like JPEG, PNG, GIF or WebP; else None."""
    for signature, ext, mime in _IMAGE_SIGNATURES:
        if head.startswith(signature):
            return ext, mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def transcript_format(mime: str | None, url: str | None = None) -> AssetFormat | None:
    """The transcript format from its MIME type, else from the URL's extension."""
    if mime:
        clean = mime.split(";", 1)[0].strip().lower()
        if clean in _MIME_FORMAT:
            return _MIME_FORMAT[clean]
    if url:
        name = posix_basename(urlsplit(url).path)
        if "." in name:
            return _EXT_FORMAT.get(name.rsplit(".", 1)[1].lower())
    return None


# --------------------------------------------------------------------------- discovery


def remote_assets_from_item(
    item_xml: str | bytes | XmlElement,
    *,
    item_id: str,
    base_url: str | None = None,
    include_artwork: bool = True,
) -> list[RemoteAsset]:
    """``podcast:chapters``, ``podcast:transcript`` and ``itunes:image`` of one RSS item."""
    element = (
        etree.fromstring(
            item_xml.encode("utf-8") if isinstance(item_xml, str) else item_xml, PARSER
        )
        if isinstance(item_xml, str | bytes)
        else item_xml
    )
    found: list[RemoteAsset] = []
    seen: set[tuple[AssetKind, str | None, AssetFormat | None]] = set()

    def add(asset: RemoteAsset) -> None:
        key = (asset.kind, asset.language, asset.format)
        if key in seen:
            return
        seen.add(key)
        found.append(asset)

    if include_artwork:
        image = element.find(tag(NS_ITUNES, "image"))
        href = _href(image, "href")
        if href:
            add(RemoteAsset(AssetKind.artwork, _resolve(base_url, href), item_id=item_id))
    chapters = element.find(tag(NS_PODCAST, "chapters"))
    href = _href(chapters, "url")
    if href:
        add(
            RemoteAsset(
                AssetKind.chapters,
                _resolve(base_url, href),
                item_id=item_id,
                mime=(chapters.get("type") or CHAPTERS_MIME).strip()
                if chapters is not None
                else None,
                format=AssetFormat.json,
            )
        )
    for transcript in element.iterfind(tag(NS_PODCAST, "transcript")):
        href = _href(transcript, "url")
        if not href:
            continue
        mime = (transcript.get("type") or "").strip() or None
        fmt = transcript_format(mime, href)
        if fmt is None:
            continue
        language = (transcript.get("language") or "").strip() or None
        add(
            RemoteAsset(
                AssetKind.transcript,
                _resolve(base_url, href),
                item_id=item_id,
                mime=mime,
                language=language,
                format=fmt,
            )
        )
    return found


def feed_artwork_asset(url: str) -> RemoteAsset:
    return RemoteAsset(AssetKind.artwork, url, item_id=None)


def _href(element: XmlElement | None, attribute: str) -> str | None:
    if element is None:
        return None
    value = (element.get(attribute) or "").strip()
    if not value and element.text:
        value = element.text.strip()
    return value or None


def _resolve(base: str | None, url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme and parts.netloc) or not base:
        return url
    return urljoin(base, url)


# --------------------------------------------------------------------------- mirroring


def mirror_asset(
    asset: RemoteAsset, assets_dir: Path, *, client: httpx.Client | None = None
) -> MirroredAsset:
    """Download ``asset`` into ``assets_dir`` under its plan basename, enforcing the limits."""
    limit = LIMITS[asset.kind]
    try:
        fetched = fetch(asset.url, max_bytes=limit, client=client)
    except BodyTooLarge as exc:
        raise AssetError(f"{asset.kind.value} exceeds {limit} bytes: {asset.url}") from exc
    except SourceError as exc:
        raise AssetError(str(exc)) from exc
    body = fetched.body
    if not body:
        raise AssetError(f"{asset.kind.value} is empty: {asset.url}")
    if asset.kind is AssetKind.artwork:
        sniffed = sniff_image(body[:16])
        if sniffed is None:
            raise AssetError(
                f"not an image ({fetched.content_type or 'unknown type'}): {asset.url}"
            )
        ext, mime = sniffed
        fmt: AssetFormat | None = None
    elif asset.kind is AssetKind.chapters:
        try:
            json.loads(body)
        except ValueError as exc:
            raise AssetError(f"chapters are not JSON: {asset.url}") from exc
        ext, mime, fmt = "json", CHAPTERS_MIME, AssetFormat.json
    else:
        fmt = asset.format or transcript_format(asset.mime or fetched.content_type, asset.url)
        if fmt is None:
            raise AssetError(f"unknown transcript format: {asset.url}")
        ext, mime = FORMAT_EXT[fmt], FORMAT_MIME[fmt]
    name = asset_basename(
        asset.kind,
        ext=ext,
        item_id=asset.item_id,
        language=asset.language if asset.kind is AssetKind.transcript else None,
        provenance=asset.provenance,
    )
    path = write_atomic(assets_dir / name, body)
    return MirroredAsset(
        kind=asset.kind,
        item_id=asset.item_id,
        language=asset.language if asset.kind is AssetKind.transcript else None,
        format=fmt,
        provenance=asset.provenance,
        basename=name,
        path=path,
        mime=mime,
        size_bytes=len(body),
    )


def write_atomic(path: Path, data: bytes) -> Path:
    """Write ``data`` next to ``path`` and ``os.replace`` it into place, fsynced."""
    tmp = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise AssetError(f"cannot write {path.name}: {exc.strerror or exc}") from exc
    return path


__all__ = [
    "ARTWORK_MAX_BYTES",
    "CHAPTERS_MAX_BYTES",
    "CHAPTERS_MIME",
    "FORMAT_EXT",
    "FORMAT_MIME",
    "LIMITS",
    "TRANSCRIPT_MAX_BYTES",
    "AssetError",
    "MirroredAsset",
    "RemoteAsset",
    "asset_basename",
    "feed_artwork_asset",
    "language_tag",
    "mirror_asset",
    "remote_assets_from_item",
    "sniff_image",
    "transcript_format",
    "write_atomic",
]
