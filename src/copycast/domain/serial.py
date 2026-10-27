"""Serial Inboxes: the posting schedule, numbered titles and progress notes (ADR 0015).

A Serial Inbox publishes its Episodes in the order the agent gave, oldest
first. Podcast apps order by ``pubDate``, so each position gets its own day,
counted back from the creation day so the last Episode is dated yesterday,
and a posting time picked for fun from :data:`POSTING_TIMES` (one per day, so
the time never decides the order).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Final

POSTING_TIMES: Final[tuple[tuple[time, str], ...]] = (
    (time(1, 23), "one, two, three"),
    (time(3, 14), "pi o'clock"),
    (time(4, 4), "posting time not found"),
    (time(9, 41), "keynote o'clock"),
    (time(10, 10), "the watch-ad hour"),
    (time(11, 11), "make a wish"),
    (time(12, 34), "straight ascending"),
    (time(13, 37), "leet o'clock"),
    (time(15, 15), "palindromic tea time"),
    (time(20, 48), "the 2048 tile"),
    (time(21, 12), "a palindrome after dinner"),
    (time(22, 22), "all twos"),
    (time(23, 59), "just before midnight"),
)
"""Posting times that read as a joke, cycled by position; the dates keep the order."""


@dataclass(frozen=True, slots=True)
class SerialEntry:
    """One Episode of a Serial Inbox as the agent described it, in listening order."""

    title: str
    duration_seconds: int | None = None
    part: int | None = None
    part_title: str | None = None
    chapter: int | None = None


def posting_dates(count: int, *, now: datetime) -> list[datetime]:
    """``count`` strictly increasing UTC dates, one day apart, the last one yesterday."""
    today = now.astimezone(UTC).date()
    dates: list[datetime] = []
    for index in range(count):
        day = today - timedelta(days=count - index)
        at, _ = POSTING_TIMES[index % len(POSTING_TIMES)]
        dates.append(datetime.combine(day, at, tzinfo=UTC))
    return dates


def numbered_title(position: int, total: int, title: str) -> str:
    """``"07 · Title"``: the position zero-padded to the width of ``total``."""
    return f"{position:0{len(str(total))}d} · {title}"


def clock(seconds: int) -> str:
    """``h:mm:ss`` (or ``m:ss`` under an hour)."""
    hours, rest = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def progress_notes(entries: list[SerialEntry]) -> list[str]:
    """One progress line per entry: where it sits in the work, by count and by time.

    ``"Part 2: The Return · Chapter 5 · Episode 7 of 42 · starts 2:13:05 into 18:04:00 (12%)"``;
    the time half is left out unless every duration is known.
    """
    total = len(entries)
    durations = [entry.duration_seconds for entry in entries]
    known = all(d is not None for d in durations)
    work = sum(d or 0 for d in durations)
    lines: list[str] = []
    elapsed = 0
    for index, entry in enumerate(entries):
        parts: list[str] = []
        if entry.part is not None or entry.part_title:
            label = f"Part {entry.part}" if entry.part is not None else "Part"
            parts.append(f"{label}: {entry.part_title}" if entry.part_title else label)
        if entry.chapter is not None:
            parts.append(f"Chapter {entry.chapter}")
        parts.append(f"Episode {index + 1} of {total}")
        if known and work > 0:
            percent = elapsed * 100 // work
            parts.append(f"starts {clock(elapsed)} into {clock(work)} ({percent}%)")
        lines.append(" · ".join(parts))
        elapsed += entry.duration_seconds or 0
    return lines


__all__ = [
    "POSTING_TIMES",
    "SerialEntry",
    "clock",
    "numbered_title",
    "posting_dates",
    "progress_notes",
]
