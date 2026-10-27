"""Tagged copies of ``tests/fixtures/media/tiny.mp3`` for upload tests."""

from __future__ import annotations

from pathlib import Path

from mutagen.easyid3 import EasyID3

from tests.support.origin import FIXTURES_DIR

TINY_MP3 = FIXTURES_DIR / "media" / "tiny.mp3"


def tagged_mp3(directory: Path, name: str, **tags: str) -> bytes:
    """``tiny.mp3`` with ID3 tags (``title``, ``album``, ``tracknumber``, ``discnumber``...)."""
    path = directory / name
    path.write_bytes(TINY_MP3.read_bytes())
    id3 = EasyID3()
    for key, value in tags.items():
        id3[key] = value
    id3.save(path)  # pyright: ignore[reportUnknownMemberType]
    return path.read_bytes()
