"""Read an uploaded audio file's tags with mutagen: ID3 (mp3), MP4 atoms (m4a, m4b), Vorbis.

Every field is best effort: a missing or unreadable tag is ``None``, never an error.
Chapters come from ID3 ``CHAP`` frames or the MP4 chapter list (Nero or QuickTime).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mutagen
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from copycast.application.models import AudioChapter, AudioTags
from copycast.logging import get_logger

log = get_logger(__name__)

_EASY_KEYS = {
    "title": "title",
    "album": "album",
    "artist": "artist",
    "albumartist": "album_artist",
    "genre": "genre",
    "date": "year",
}


def _first(value: Any) -> str | None:
    if isinstance(value, list | tuple):
        items: list[Any] = list(value)  # pyright: ignore[reportUnknownArgumentType]
        value = items[0] if items else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pair(value: str | None) -> tuple[int | None, int | None]:
    """``"3/12"`` -> (3, 12); ``"3"`` -> (3, None)."""
    if not value:
        return None, None
    number, _, total = value.partition("/")

    def num(text: str) -> int | None:
        text = text.strip()
        return int(text) if text.isdigit() else None

    return num(number), num(total)


def _id3_chapters(path: Path) -> list[AudioChapter]:
    try:
        tags: Any = ID3(path)
    except Exception:
        return []
    chapters: list[AudioChapter] = []
    for frame in tags.getall("CHAP"):
        title: str | None = None
        sub = frame.sub_frames.get("TIT2")
        if sub is not None:
            title = _first(sub.text)
        chapters.append(AudioChapter(start_seconds=frame.start_time / 1000, title=title))
    return sorted(chapters, key=lambda chapter: chapter.start_seconds)


def _mp4_chapters(path: Path) -> list[AudioChapter]:
    try:
        found: Any = MP4(path).chapters
    except Exception:
        return []
    if found is None:
        return []
    chapters: list[AudioChapter] = []
    for chapter in found:
        entry: Any = chapter
        chapters.append(AudioChapter(start_seconds=float(entry.start), title=entry.title or None))
    return chapters


def read_audio_tags(path: Path) -> AudioTags | None:
    """The tags of ``path``, or ``None`` when mutagen does not recognise it as audio."""
    try:
        easy = mutagen.File(path, easy=True)  # pyright: ignore[reportUnknownMemberType, reportPrivateImportUsage]
    except Exception as exc:
        log.info("upload.tags_unreadable", path=str(path), error=str(exc))
        return None
    if easy is None:
        return None
    raw: Any = easy
    values: dict[str, Any] = {}
    tags: Any = raw.tags or {}
    for key, field in _EASY_KEYS.items():
        try:
            values[field] = _first(tags.get(key))
        except Exception:
            values[field] = None
    values["track"], values["track_total"] = _pair(_first(tags.get("tracknumber")))
    values["disc"], values["disc_total"] = _pair(_first(tags.get("discnumber")))
    length = getattr(raw.info, "length", None)
    values["duration_seconds"] = round(length) if isinstance(length, int | float) else None
    mimes: list[str] = list(getattr(raw, "mime", []) or [])
    values["mime"] = mimes[0] if mimes else None
    chapters = _id3_chapters(path) or _mp4_chapters(path)
    return AudioTags(**values, chapters=chapters)


__all__ = ["read_audio_tags"]
