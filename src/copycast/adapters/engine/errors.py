"""Map yt-dlp's exceptions onto the Engine port's error classes.

This module deliberately does not import ``yt_dlp`` (``ytdlp.py`` is the
only importer): exceptions are recognised by the class names in their MRO
and by the attributes yt-dlp's networking errors carry (``status``), so the
rules can be unit-tested with plain look-alike classes.
"""

from __future__ import annotations

import errno
import re
from collections.abc import Iterator
from typing import cast

from copycast.application.ports import (
    Cancelled,
    EngineError,
    PermanentError,
    StorageFull,
    TransientError,
)

STORAGE_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})
PERMANENT_HTTP_STATUSES = frozenset({401, 403, 404, 410})

_STORAGE_TEXT = re.compile(r"no space left on device|disk quota exceeded", re.IGNORECASE)
_PERMANENT_TEXT = re.compile(
    r"private video|video unavailable|is unavailable|not available|has been removed|"
    r"no longer available|members-only|join this channel|geo.?restrict|"
    r"not available in your country|account.*terminated|unsupported url|"
    r"no video formats|requested format is not available|no audio|"
    r"is not a valid url|sign in to confirm|age.restrict|does not exist|"
    r"http error (?:401|403|404|410)\b|not supported|unsupported|"
    r"\bonly\b[^.\n]{0,80}\b(?:is|are) supported\b",
    re.IGNORECASE,
)
_TRANSIENT_TEXT = re.compile(
    r"http error (?:408|425|429|5\d\d)\b|time(?:d)? ?out|temporar|connection|network|"
    r"throttl|rate.?limit|try again|reset by peer|name or service not known|"
    r"nodename nor servname|eof occurred|incomplete read|content too short|"
    r"unable to download|unable to extract|unable to open|unable to write",
    re.IGNORECASE,
)


def _mro_names(exc: BaseException) -> frozenset[str]:
    return frozenset(cls.__name__ for cls in type(exc).__mro__)


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` followed by everything it wraps: causes, contexts, ``exc_info`` and args."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        candidates: list[object] = [
            current.__cause__,
            current.__context__,
            getattr(current, "cause", None),
            *cast(tuple[object, ...], current.args),
        ]
        exc_info = cast(object, getattr(current, "exc_info", None))
        if isinstance(exc_info, tuple):
            parts = cast(tuple[object, ...], exc_info)
            if len(parts) == 3:
                candidates.append(parts[1])
        for candidate in candidates:
            if isinstance(candidate, BaseException) and id(candidate) not in seen:
                stack.append(candidate)


def _status_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) and "HTTPError" in _mro_names(exc) else None


def _message(exc: BaseException) -> str:
    msg = getattr(exc, "msg", None)
    text = msg if isinstance(msg, str) and msg else str(exc)
    return text or type(exc).__name__


def _by_class(exc: BaseException) -> type[EngineError] | None:
    names = _mro_names(exc)
    if "DownloadCancelled" in names:
        return Cancelled
    if isinstance(exc, OSError) and exc.errno in STORAGE_ERRNOS:
        return StorageFull
    if _STORAGE_TEXT.search(str(exc)):
        return StorageFull
    status = _status_of(exc)
    if status is not None:
        return PermanentError if status in PERMANENT_HTTP_STATUSES else TransientError
    if "TransportError" in names or "ThrottledDownload" in names:
        return TransientError
    if "ContentTooShortError" in names:
        return TransientError
    if "GeoRestrictedError" in names or "UnsupportedError" in names:
        return PermanentError
    if "UnavailableVideoError" in names:
        return PermanentError
    if "ExtractorError" in names:
        text = str(exc)
        if _TRANSIENT_TEXT.search(text) and not _PERMANENT_TEXT.search(text):
            return TransientError
        return PermanentError if getattr(exc, "expected", False) else None
    if "PostProcessingError" in names:
        return PermanentError if _PERMANENT_TEXT.search(str(exc)) else TransientError
    return None


def classify(exc: BaseException) -> EngineError:
    """Return the Engine error that best describes ``exc`` (chained to it).

    Order of precedence, applied over the whole exception chain: an Engine
    error already raised, cancellation, storage exhaustion, HTTP statuses,
    network transport errors, expected extractor errors (permanent), other
    extractor and post-processing errors, then message heuristics. Anything
    unrecognised is transient so the worker retries with backoff.
    """
    chain = list(_chain(exc))
    for item in chain:
        if isinstance(item, EngineError):
            return item
    for item in chain:
        if "DownloadCancelled" in _mro_names(item):
            return _wrap(Cancelled, item, exc)
    for item in chain:
        if _by_class(item) is StorageFull:
            return _wrap(StorageFull, item, exc)
    for item in chain:
        kind = _by_class(item)
        if kind is not None:
            return _wrap(kind, item, exc)
    text = " ".join(_message(item) for item in chain)
    if _TRANSIENT_TEXT.search(text) and not _PERMANENT_TEXT.search(text):
        return _wrap(TransientError, exc, exc)
    if _PERMANENT_TEXT.search(text):
        return _wrap(PermanentError, exc, exc)
    return _wrap(TransientError, exc, exc)


def _wrap(kind: type[EngineError], reason: BaseException, original: BaseException) -> EngineError:
    message = _message(reason)
    if reason is not original:
        head = _message(original)
        if message not in head:
            message = f"{head} ({message})"
    wrapped = kind(message)
    wrapped.__cause__ = original
    return wrapped


__all__ = [
    "PERMANENT_HTTP_STATUSES",
    "STORAGE_ERRNOS",
    "Cancelled",
    "EngineError",
    "PermanentError",
    "StorageFull",
    "TransientError",
    "classify",
]
