from __future__ import annotations

from copycast.domain.ordering import natural_key


def test_digit_runs_sort_as_numbers() -> None:
    names = ["Chapter 10.mp3", "chapter 2.mp3", "Chapter 1.mp3", "Intro.mp3", "Chapter 02b.mp3"]
    assert sorted(names, key=natural_key) == [
        "Chapter 1.mp3",
        "chapter 2.mp3",
        "Chapter 02b.mp3",
        "Chapter 10.mp3",
        "Intro.mp3",
    ]


def test_ties_fall_back_to_the_name() -> None:
    assert sorted(["b01.mp3", "B1.mp3"], key=natural_key) == ["B1.mp3", "b01.mp3"]
