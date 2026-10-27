"""Chapters: yt-dlp's ``chapters`` list as the Podcasting 2.0 document a feed advertises.

The Engine reports chapters as ``[{"start_time": 0.0, "end_time": 63.0, "title": "Intro"}]``;
a ``podcast:chapters`` link of type ``application/json+chapters`` promises
``{"version": "1.2.0", "chapters": [{"startTime": 0, "endTime": 63, "title": "Intro"}]}``,
which is what podcast apps and the web player read. :func:`podcast_chapters` converts the
first into the second; :func:`upgraded_document` says what a chapters file already on disk
should become (archives before 1.4 wrote the Engine's list verbatim).

Times are seconds, an ``int`` when whole and a ``float`` rounded to milliseconds otherwise.
``startTime`` is required by the specification and ``title`` optional, so an entry without a
finite, non-negative start is dropped and a blank title is left out.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final, cast

CHAPTERS_VERSION: Final = "1.2.0"
"""The Podcasting 2.0 JSON chapters version the documents claim."""


def podcast_chapters(chapters: object) -> dict[str, Any] | None:
    """yt-dlp's ``chapters`` list -> a Podcasting 2.0 document, ordered by start.

    ``None`` when nothing in ``chapters`` has a usable start (an empty list, a value
    that is not a list): there is then no chapters file to write.
    """
    if not isinstance(chapters, list):
        return None
    converted: list[dict[str, Any]] = []
    for entry in cast(list[object], chapters):
        chapter = _chapter(entry)
        if chapter is not None:
            converted.append(chapter)
    if not converted:
        return None
    converted.sort(key=lambda chapter: chapter["startTime"])  # stable: ties keep their order
    return {"version": CHAPTERS_VERSION, "chapters": converted}


def upgraded_document(document: object) -> dict[str, Any] | None:
    """What a chapters file holding ``document`` (parsed JSON) should be rewritten to.

    A list is the Engine's shape and is converted. ``None`` means "leave the file as it
    is": a Podcasting 2.0 document (an object with a ``chapters`` list, which is also what
    Source chapters are), anything else that is not ours, and a list in which no entry
    has a usable start, so a file this module cannot read is never replaced by an empty one.
    """
    if not isinstance(document, list):
        return None
    return podcast_chapters(cast(list[object], document))


def _chapter(entry: object) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    fields = cast(Mapping[str, Any], entry)
    start = _seconds(fields.get("start_time"))
    if start is None:
        return None
    chapter: dict[str, Any] = {"startTime": start}
    end = _seconds(fields.get("end_time"))
    if end is not None and end > start:
        chapter["endTime"] = end
    title = fields.get("title")
    if isinstance(title, str) and title.strip():
        chapter["title"] = title.strip()
    return chapter


def _seconds(value: object) -> int | float | None:
    """A finite, non-negative number of seconds: an ``int`` when whole, else milliseconds."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        seconds = float(value)
    except OverflowError:  # an int beyond any float
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    rounded = round(seconds, 3)
    return int(rounded) if rounded.is_integer() else rounded


__all__ = ["CHAPTERS_VERSION", "podcast_chapters", "upgraded_document"]
