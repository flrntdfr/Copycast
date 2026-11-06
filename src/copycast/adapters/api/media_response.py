"""An Episode's archived media and an item's assets, served from the Object store.

Shared by the podcast routes under ``/feeds`` (``routes/public.py``) and the web
player's routes under ``/api`` (``routes/play.py``): both proxy the stored object with
``ObjectResponse`` (Range 206/416, HEAD, the object's strong ETag, ``If-Range``) and
count a download by the same rule (:func:`counts_as_download`, ADR 0010). The web
player's answers also carry a file name (:func:`download_name`) in
``Content-Disposition``, ``inline`` or, for a download link, ``attachment``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Final
from urllib.parse import quote

from starlette.background import BackgroundTask

from copycast.adapters.api.objects import ObjectResponse
from copycast.application.models import AssetRead, ItemRead

DOWNLOAD_NAME_MAX: Final = 120
"""Characters of the item title kept in a download's file name (before the extension)."""
_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
_SPACES = re.compile(r"\s+")


def counts_as_download(method: str, range_header: str | None) -> bool:
    """Full GET or a Range starting at byte 0; HEAD and later ranges never count."""
    if method.upper() != "GET":
        return False
    if range_header is None:
        return True
    value = range_header.strip().lower()
    if not value.startswith("bytes="):
        return False
    first = value.removeprefix("bytes=").split(",", 1)[0].strip()
    return first.startswith("0-")


async def media_object(
    layout: Any,
    feed_id: str,
    item: ItemRead,
    *,
    headers: dict[str, str],
    background: BackgroundTask | None = None,
) -> ObjectResponse | None:
    """The archived media object of ``item`` streamed; None when it has none or it is gone."""
    if item.media is None:
        return None
    store = layout.store
    obj = await store.head_async(layout.media_key(feed_id, item.id, item.media.ext))
    if obj is None:
        return None
    return ObjectResponse(
        store, obj, media_type=item.media.mime, headers=headers, background=background
    )


async def asset_object(
    layout: Any, feed_id: str, asset: AssetRead, *, headers: dict[str, str]
) -> ObjectResponse | None:
    """An archived item asset by its file name (every one lives under ``assets/``)."""
    if asset.name is None:
        return None
    try:
        key: str = layout.asset_key(feed_id, asset.name)
    except ValueError:
        return None
    obj = await layout.store.head_async(key)
    if obj is None:
        return None
    return ObjectResponse(
        layout.store,
        obj,
        media_type=asset.mime or "application/octet-stream",
        headers=headers,
    )


def _clean(title: str) -> str:
    """A title as a file name: no path separators, quotes, control or reserved characters,
    no leading dots, single spaces, at most :data:`DOWNLOAD_NAME_MAX` characters."""
    text = unicodedata.normalize("NFC", title)
    text = _UNSAFE_NAME.sub(" ", text)
    text = _SPACES.sub(" ", text).strip().lstrip(".").strip()
    return text[:DOWNLOAD_NAME_MAX].rstrip(" .")


def download_name(item: ItemRead) -> str:
    """``"<title>.<ext>"``: the item title made safe for a file name (else the item id),
    plus the archived media's own extension."""
    stem = _clean(item.title or "") or item.id
    ext = item.media.ext if item.media is not None else ""
    return f"{stem}.{ext}" if ext else stem


def content_disposition(name: str, *, attachment: bool = False) -> str:
    """``inline`` (or ``attachment``) with an ASCII ``filename`` and, for a name that is not
    plain ASCII, RFC 6266's ``filename*=UTF-8''…``; quotes and backslashes never reach it."""
    disposition = "attachment" if attachment else "inline"
    printable = name.encode("ascii", "ignore").decode()
    ascii_name = "".join(ch for ch in printable if " " <= ch < "\x7f" and ch not in '"\\').strip()
    header = f'{disposition}; filename="{ascii_name or "download"}"'
    if ascii_name != name:
        header += f"; filename*=UTF-8''{quote(name, safe='')}"
    return header


__all__ = [
    "DOWNLOAD_NAME_MAX",
    "asset_object",
    "content_disposition",
    "counts_as_download",
    "download_name",
    "media_object",
]
