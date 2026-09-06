"""``errors.classify`` over look-alike classes (same names as yt-dlp's) and the real ones."""

from __future__ import annotations

import errno
from typing import Any

import pytest
from yt_dlp.utils import (
    DownloadCancelled,
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    MaxDownloadsReached,
    PostProcessingError,
    UnavailableVideoError,
)

from copycast.adapters.engine.errors import classify
from copycast.application.ports import (
    Cancelled,
    PermanentError,
    StorageFull,
    TransientError,
)


class HTTPError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"HTTP Error {status}")


class TransportError(Exception):
    pass


class ThrottledDownload(Exception):
    pass


def _download_error(inner: BaseException, message: str = "ERROR: boom") -> DownloadError:
    """A ``DownloadError`` the way ``YoutubeDL.trouble`` builds it (with ``exc_info``)."""
    try:
        raise inner
    except BaseException:
        import sys

        return DownloadError(message, sys.exc_info())


def test_engine_errors_pass_through_unchanged() -> None:
    original = PermanentError("gone")
    assert classify(original) is original
    wrapped = _download_error(original)
    assert classify(wrapped) is original


def test_cancellation_wins_over_everything() -> None:
    assert isinstance(classify(DownloadCancelled("cancelled by Copycast")), Cancelled)
    assert isinstance(classify(MaxDownloadsReached()), Cancelled)
    nested = _download_error(DownloadCancelled("stop"), "ERROR: unable to download video data")
    assert isinstance(classify(nested), Cancelled)


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_storage_full_from_errno_even_when_wrapped_as_unavailable(code: int) -> None:
    os_error = OSError(code, "No space left on device")
    assert isinstance(classify(os_error), StorageFull)
    assert isinstance(classify(UnavailableVideoError(os_error)), StorageFull)
    assert isinstance(classify(_download_error(os_error, "unable to write data")), StorageFull)


def test_storage_full_from_message() -> None:
    err = PostProcessingError("ffmpeg: No space left on device")
    assert isinstance(classify(err), StorageFull)


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, PermanentError),
        (403, PermanentError),
        (404, PermanentError),
        (410, PermanentError),
        (429, TransientError),
        (500, TransientError),
        (503, TransientError),
        (408, TransientError),
    ],
)
def test_http_statuses(status: int, kind: type[Exception]) -> None:
    http = HTTPError(status)
    assert isinstance(classify(http), kind)
    extractor = ExtractorError(f"Unable to download webpage: HTTP Error {status}", cause=http)
    assert isinstance(classify(extractor), kind)


def test_expected_extractor_errors_are_permanent() -> None:
    assert isinstance(classify(ExtractorError("Private video", expected=True)), PermanentError)
    assert isinstance(classify(GeoRestrictedError("blocked", countries=["US"])), PermanentError)
    assert isinstance(classify(UnavailableVideoError("gone")), PermanentError)
    members = _download_error(ExtractorError("Join this channel to get access", expected=True))
    assert isinstance(classify(members), PermanentError)


def test_network_flavoured_extractor_errors_are_transient() -> None:
    assert isinstance(classify(TransportError("Connection reset by peer")), TransientError)
    assert isinstance(classify(ThrottledDownload()), TransientError)
    err = ExtractorError("Unable to download webpage: timed out", cause=TransportError("t"))
    assert isinstance(classify(err), TransientError)
    unexpected = ExtractorError("Unable to extract player version")
    assert isinstance(classify(unexpected), TransientError)


def test_postprocessing_errors() -> None:
    assert isinstance(classify(PostProcessingError("ffmpeg exited with code 1")), TransientError)
    assert isinstance(classify(PostProcessingError("Only mp3 is supported")), PermanentError)
    no_audio = _download_error(PostProcessingError("no audio stream"), "Postprocessing: no audio")
    assert isinstance(classify(no_audio), PermanentError)


def test_unknown_exceptions_are_transient_and_keep_the_cause() -> None:
    boom = RuntimeError("something odd")
    result = classify(boom)
    assert isinstance(result, TransientError)
    assert result.__cause__ is boom
    assert "something odd" in str(result)


def test_message_heuristics_when_no_class_matches() -> None:
    assert isinstance(classify(RuntimeError("Video unavailable")), PermanentError)
    assert isinstance(classify(RuntimeError("Unsupported URL: x")), PermanentError)
    assert isinstance(classify(RuntimeError("Read timed out")), TransientError)


def test_wrapped_message_mentions_both_layers() -> None:
    inner: Any = ExtractorError("Video unavailable", expected=True)
    outer = _download_error(inner, "ERROR: [youtube] abc: Video unavailable")
    result = classify(outer)
    assert isinstance(result, PermanentError)
    assert "Video unavailable" in str(result)
