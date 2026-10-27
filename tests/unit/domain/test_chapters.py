"""yt-dlp chapters -> Podcasting 2.0 chapters, and the rewrite of files archived raw."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from copycast.domain.chapters import CHAPTERS_VERSION, podcast_chapters, upgraded_document


def test_converts_the_engine_shape() -> None:
    raw = [
        {"start_time": 0.0, "end_time": 63.0, "title": "Intro"},
        {"start_time": 63.0, "end_time": 1830.5, "title": "  The interview  "},
        {"start_time": 1830.5, "end_time": 1900.0, "title": "Outro"},
    ]
    assert podcast_chapters(raw) == {
        "version": "1.2.0",
        "chapters": [
            {"startTime": 0, "endTime": 63, "title": "Intro"},
            {"startTime": 63, "endTime": 1830.5, "title": "The interview"},
            {"startTime": 1830.5, "endTime": 1900, "title": "Outro"},
        ],
    }
    assert CHAPTERS_VERSION == "1.2.0"


def test_whole_seconds_are_ints_and_the_rest_milliseconds() -> None:
    document = podcast_chapters(
        [
            {"start_time": 12.34567, "end_time": 20.0004},
            {"start_time": 7, "end_time": 9.9996},
            {"start_time": 0.0004},
        ]
    )
    assert document is not None
    chapters = document["chapters"]
    assert chapters == [
        {"startTime": 0},
        {"startTime": 7, "endTime": 10},
        {"startTime": 12.346, "endTime": 20},
    ]
    assert [type(c["startTime"]) for c in chapters] == [int, int, float]
    # The document is plain JSON (no NaN, no tuples) and round-trips unchanged.
    assert json.loads(json.dumps(document, allow_nan=False)) == document


def test_entries_are_sorted_by_start_and_ties_keep_their_order() -> None:
    document = podcast_chapters(
        [
            {"start_time": 30, "title": "C"},
            {"start_time": 0, "title": "A"},
            {"start_time": 30, "title": "D"},
            {"start_time": 10, "title": "B"},
        ]
    )
    assert document is not None
    assert [c["title"] for c in document["chapters"]] == ["A", "B", "C", "D"]


@pytest.mark.parametrize(
    "start",
    [None, "12", -1, -0.5, math.nan, math.inf, -math.inf, True, False, 10**400, [], {}],
)
def test_an_entry_without_a_usable_start_is_dropped(start: Any) -> None:
    raw = [{"start_time": start, "title": "Bad"}, {"start_time": 5, "title": "Good"}]
    assert podcast_chapters(raw) == {
        "version": CHAPTERS_VERSION,
        "chapters": [{"startTime": 5, "title": "Good"}],
    }


def test_an_entry_missing_its_start_or_not_an_object_is_dropped() -> None:
    raw: list[Any] = [{"title": "No start"}, "Intro", 3, None, ["start_time", 0], {"start_time": 1}]
    assert podcast_chapters(raw) == {"version": CHAPTERS_VERSION, "chapters": [{"startTime": 1}]}


@pytest.mark.parametrize(
    ("end", "expected"),
    [
        (None, {"startTime": 10}),
        (10, {"startTime": 10}),  # an empty span says nothing
        (9.5, {"startTime": 10}),  # nor does one that ends before it starts
        (math.nan, {"startTime": 10}),
        (math.inf, {"startTime": 10}),
        ("20", {"startTime": 10}),
        (True, {"startTime": 10}),
        (10.0004, {"startTime": 10}),  # equal once rounded to milliseconds
        (10.001, {"startTime": 10, "endTime": 10.001}),
        (20.0, {"startTime": 10, "endTime": 20}),
    ],
)
def test_end_time_is_kept_only_when_it_follows_the_start(
    end: Any, expected: dict[str, Any]
) -> None:
    document = podcast_chapters([{"start_time": 10, "end_time": end}])
    assert document is not None and document["chapters"] == [expected]


@pytest.mark.parametrize("title", [None, "", "   ", 42, ["Intro"]])
def test_a_blank_or_missing_title_is_left_out(title: Any) -> None:
    document = podcast_chapters([{"start_time": 0, "title": title}])
    assert document == {"version": CHAPTERS_VERSION, "chapters": [{"startTime": 0}]}


def test_unicode_titles_are_kept() -> None:
    document = podcast_chapters([{"start_time": 0, "title": "Ça commence — 第一章"}])
    assert document is not None and document["chapters"][0]["title"] == "Ça commence — 第一章"


def test_other_engine_keys_are_not_carried_over() -> None:
    document = podcast_chapters([{"start_time": 0, "title": "Intro", "index": 1, "url": "x"}])
    assert document == {
        "version": CHAPTERS_VERSION,
        "chapters": [{"startTime": 0, "title": "Intro"}],
    }


@pytest.mark.parametrize(
    "chapters",
    [[], None, "chapters", 3, {"start_time": 0}, [{"title": "No start"}], [None, "x"]],
)
def test_nothing_usable_means_no_document(chapters: Any) -> None:
    assert podcast_chapters(chapters) is None


def test_the_input_is_not_modified() -> None:
    raw = [{"start_time": 5.0, "title": " B "}, {"start_time": 0.0, "title": "A"}]
    snapshot = json.dumps(raw)
    podcast_chapters(raw)
    assert json.dumps(raw) == snapshot


# --------------------------------------------------------------------------- files on disk


def test_a_raw_list_on_disk_is_upgraded() -> None:
    raw = [{"start_time": 0.0, "end_time": 63.0, "title": "Intro"}]
    assert upgraded_document(raw) == {
        "version": CHAPTERS_VERSION,
        "chapters": [{"startTime": 0, "endTime": 63, "title": "Intro"}],
    }


@pytest.mark.parametrize(
    "document",
    [
        # Podcasting 2.0 documents (ours since 1.4, and every Source's) stay as they are,
        {"version": "1.2.0", "chapters": [{"startTime": 0, "title": "Intro"}]},
        {"version": "1.2.0", "chapters": []},
        {"chapters": [{"startTime": 0}], "author": "kept"},
        # and so does whatever is not ours to fix,
        {"version": "1.2.0"},
        {"chapters": "nope"},
        "chapters",
        42,
        None,
        True,
        # including a list nothing usable can be read from: never replaced by an empty one.
        [],
        [{"startTime": 0, "title": "Not the Engine's keys"}],
        ["Intro", 3],
    ],
)
def test_anything_else_is_left_alone(document: Any) -> None:
    assert upgraded_document(document) is None


def test_upgrading_is_idempotent() -> None:
    first = upgraded_document([{"start_time": 1.5, "title": "One"}])
    assert first is not None
    assert upgraded_document(json.loads(json.dumps(first))) is None
