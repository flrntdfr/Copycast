"""Serial Bucket rules: posting dates, numbered titles, progress notes (ADR 0015)."""

from __future__ import annotations

from datetime import UTC, datetime

from copycast.domain.serial import (
    POSTING_TIMES,
    SerialEntry,
    clock,
    numbered_title,
    posting_dates,
    progress_notes,
)

NOW = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)


def test_posting_dates_are_one_day_apart_and_end_yesterday() -> None:
    dates = posting_dates(30, now=NOW)
    assert dates == sorted(dates) and len(set(d.date() for d in dates)) == 30
    assert dates[-1].date() == datetime(2026, 10, 26, tzinfo=UTC).date()
    assert dates[0].timetz().replace(tzinfo=None) == POSTING_TIMES[0][0]
    assert dates[len(POSTING_TIMES)].time() == POSTING_TIMES[0][0]
    assert all(d.tzinfo is UTC for d in dates)


def test_numbered_title_pads_to_the_total() -> None:
    assert numbered_title(7, 42, "The Return") == "07 · The Return"
    assert numbered_title(7, 9, "x") == "7 · x"
    assert numbered_title(7, 120, "x") == "007 · x"


def test_clock() -> None:
    assert clock(65) == "1:05"
    assert clock(3600 + 61) == "1:01:01"
    assert clock(-3) == "0:00"


def test_progress_notes_with_parts_chapters_and_time() -> None:
    entries = [
        SerialEntry("Prologue", 600, part=1, part_title="Departure"),
        SerialEntry("Storm", 1200, part=1, part_title="Departure", chapter=1),
        SerialEntry("Home", 1200, part=2, chapter=2),
    ]
    assert progress_notes(entries) == [
        "Part 1: Departure · Episode 1 of 3 · starts 0:00 into 50:00 (0%)",
        "Part 1: Departure · Chapter 1 · Episode 2 of 3 · starts 10:00 into 50:00 (20%)",
        "Part 2 · Chapter 2 · Episode 3 of 3 · starts 30:00 into 50:00 (60%)",
    ]


def test_progress_notes_without_every_duration_count_only() -> None:
    notes = progress_notes([SerialEntry("a", 10), SerialEntry("b", None, part_title="Coda")])
    assert notes == ["Episode 1 of 2", "Part: Coda · Episode 2 of 2"]
