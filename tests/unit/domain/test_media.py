"""predicted_ext and the extension <-> MIME tables behind stable media URLs."""

from __future__ import annotations

import pytest

from copycast.domain.enums import SourceKind
from copycast.domain.media import (
    AAC_CONTAINERS,
    DEFAULT_EXT,
    ENGINE_EXT,
    EXT_MIME,
    FALLBACK_MIME,
    KNOWN_EXTS,
    MIME_EXT,
    MPEG4_CONTAINERS,
    PODCAST_EXTS,
    TRANSCODE_EXT,
    archived_ext_for,
    clean_mime,
    ext_for_mime,
    mime_for_ext,
    predicted_ext,
    url_ext,
)


@pytest.mark.parametrize(
    ("source_kind", "enclosure_type", "enclosure_url", "expected"),
    [
        # A ytdlp Source archives to the Engine's container whatever the listing says.
        (SourceKind.ytdlp, None, None, "m4a"),
        (SourceKind.ytdlp, "audio/mpeg", "https://x/y.mp3", "m4a"),
        ("ytdlp", None, None, "m4a"),
        # RSS: the enclosure's MIME type first (it wins over the URL), as the container
        # the Engine archives it in: mp3 and m4a stay, AAC in an MPEG-4 container is
        # remuxed to m4a...
        (SourceKind.rss, "audio/mpeg", "https://x/y.m4a", "mp3"),
        (SourceKind.rss, "audio/mp4", None, "m4a"),
        (SourceKind.rss, "audio/x-m4a", None, "m4a"),
        (SourceKind.rss, "video/mp4", None, "m4a"),
        (SourceKind.rss, "video/x-m4v", None, "m4a"),
        (SourceKind.rss, "video/quicktime", None, "m4a"),
        # ...and everything else is advertised as the mp3 it is transcoded to (an
        # `{id}.ogg` URL would serve an mp3 under audio/mpeg for its whole life)...
        (SourceKind.rss, "audio/aac", None, "mp3"),
        (SourceKind.rss, "audio/ogg; codecs=opus", None, "mp3"),
        (SourceKind.rss, "AUDIO/OPUS", None, "mp3"),
        (SourceKind.rss, "audio/flac", None, "mp3"),
        (SourceKind.rss, "audio/wav", None, "mp3"),
        (SourceKind.rss, "audio/x-wav", None, "mp3"),
        (SourceKind.rss, "audio/webm", None, "mp3"),
        (SourceKind.rss, "video/webm", None, "mp3"),
        # ...then the URL's known extension, the same way...
        (SourceKind.rss, "application/octet-stream", "https://x/y.M4A?dl=1", "m4a"),
        (SourceKind.rss, "application/octet-stream", "https://x/y.mp4?dl=1", "m4a"),
        (SourceKind.rss, "application/octet-stream", "https://x/y.FLAC?dl=1", "mp3"),
        ("rss", None, "https://x/dir/y.ogg", "mp3"),
        (SourceKind.rss, "", "https://x/y.opus#t", "mp3"),
        # ...else mp3.
        (SourceKind.rss, None, "https://x/y", "mp3"),
        (SourceKind.rss, "text/html", "https://x/y.pdf", "mp3"),
        (SourceKind.rss, None, "https://x/y.zip", "mp3"),
        (SourceKind.rss, None, None, "mp3"),
        # An Inbox (no Source) is filled by the Engine, unless the Request named a media file.
        (None, None, None, "m4a"),
        (None, "audio/mpeg", None, "mp3"),
        (None, None, "https://x/talk.mp3", "mp3"),
        (None, "audio/ogg", "https://x/talk.ogg", "mp3"),
        (None, None, "https://www.youtube.com/watch?v=abc", "m4a"),
    ],
)
def test_predicted_ext(
    source_kind: SourceKind | str | None,
    enclosure_type: str | None,
    enclosure_url: str | None,
    expected: str,
) -> None:
    assert predicted_ext(source_kind, enclosure_type, enclosure_url) == expected


def test_predicted_ext_defaults() -> None:
    assert predicted_ext(SourceKind.rss) == DEFAULT_EXT == "mp3"
    assert predicted_ext(SourceKind.ytdlp) == ENGINE_EXT == "m4a"
    assert predicted_ext(None) == ENGINE_EXT


@pytest.mark.parametrize(
    ("ext", "expected"),
    [
        ("mp3", "mp3"),
        ("M4A", "m4a"),
        (".m4a", "m4a"),
        ("mp4", "m4a"),
        ("m4v", "m4a"),
        ("mov", "m4a"),
        ("m4b", "mp3"),  # not a container the normalizer keeps or remuxes
        ("aac", "mp3"),
        ("ogg", "mp3"),
        ("opus", "mp3"),
        ("webm", "mp3"),
        ("mkv", "mp3"),
        ("flac", "mp3"),
        ("wav", "mp3"),
        ("aiff", "mp3"),
        ("bin", "mp3"),
    ],
)
def test_archived_ext_for(ext: str, expected: str) -> None:
    """The container the Engine leaves: kept, remuxed to m4a, or transcoded to mp3."""
    assert archived_ext_for(ext) == expected


def test_container_tables_agree() -> None:
    assert (
        PODCAST_EXTS <= KNOWN_EXTS and TRANSCODE_EXT in PODCAST_EXTS and ENGINE_EXT in PODCAST_EXTS
    )
    assert MPEG4_CONTAINERS < AAC_CONTAINERS
    # Every prediction is a container the Engine actually produces.
    for mime in MIME_EXT:
        assert predicted_ext(SourceKind.rss, mime) in PODCAST_EXTS


@pytest.mark.parametrize(
    ("ext", "mime"),
    [
        ("mp3", "audio/mpeg"),
        (".MP3", "audio/mpeg"),
        ("m4a", "audio/mp4"),
        ("m4b", "audio/mp4"),
        ("mp4", "audio/mp4"),
        ("aac", "audio/aac"),
        ("ogg", "audio/ogg"),
        ("oga", "audio/ogg"),
        ("opus", "audio/opus"),
        ("webm", "audio/webm"),
        ("flac", "audio/flac"),
        ("wav", "audio/wav"),
        ("aiff", "audio/aiff"),
        ("mka", "audio/x-matroska"),
        ("mov", "video/quicktime"),
        ("ogv", "video/ogg"),
        ("weird", FALLBACK_MIME),
        ("", FALLBACK_MIME),
    ],
)
def test_mime_for_ext(ext: str, mime: str) -> None:
    assert mime_for_ext(ext) == mime


def test_tables_agree() -> None:
    """Every predicted extension has a MIME type to serve it under, and the round trip holds."""
    assert set(MIME_EXT.values()) <= KNOWN_EXTS == set(EXT_MIME)
    for mime, ext in MIME_EXT.items():
        assert ext_for_mime(mime) == ext
        assert mime_for_ext(ext) != FALLBACK_MIME
    # A served MIME type predicts the extension it was served for.
    for ext in ("mp3", "m4a", "aac", "ogg", "opus", "webm", "flac", "wav"):
        assert ext_for_mime(mime_for_ext(ext)) == ext


def test_clean_mime_ext_for_mime_and_url_ext() -> None:
    assert clean_mime(" Audio/MPEG ; charset=binary") == "audio/mpeg"
    assert clean_mime("") is None and clean_mime(None) is None and clean_mime(" ; ") is None
    assert ext_for_mime("audio/MP4; codecs=mp4a") == "m4a"
    assert ext_for_mime("application/octet-stream") is None and ext_for_mime(None) is None
    assert url_ext("https://x/a.b.M4A") == "m4a"
    assert url_ext("https://x/a.mp3?token=1&x=y.ogg") == "mp3"
    assert url_ext("https://x/noext") is None
    assert url_ext("https://x/archive.zip") is None
    assert url_ext("https://x/dir.with.dots/") is None
    assert url_ext("") is None and url_ext(None) is None
