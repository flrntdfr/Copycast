"""Selections: "1-42, 180" parsing and resolution against a Catalog."""

from __future__ import annotations

import pytest

from copycast.domain.enums import Numbering
from copycast.domain.exceptions import InvalidSelection
from copycast.domain.selection import (
    MAX_SELECTION_SIZE,
    parse_selection,
    parse_selection_strict,
    resolve_selection,
)
from tests.support.factories import selection_candidates


def test_parse_canonical_example() -> None:
    parsed = parse_selection("1-42,180")
    assert parsed.ok
    assert parsed.numbers == [*range(1, 43), 180]
    assert len(parsed.numbers) == 43


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("1-42, 180", [*range(1, 43), 180]),
        (" 5 ", [5]),
        ("3,1,2", [3, 1, 2]),
        ("1-3,2-4", [1, 2, 3, 4]),
        ("7;8 9\n10", [7, 8, 9, 10]),
        ("2..4", [2, 3, 4]),
        ("2\u20134", [2, 3, 4]),
        ("5 - 6", [5, 6]),
        ("", []),
        ("   ", []),
    ],
)
def test_parse_variants(text: str, numbers: list[int]) -> None:
    parsed = parse_selection(text)
    assert parsed.ok, parsed.errors
    assert parsed.numbers == numbers


@pytest.mark.parametrize(
    "text",
    ["abc", "1-", "-3", "0", "0-2", "5-3", "1,x", "1.5", ","],
)
def test_parse_errors_are_collected_not_raised(text: str) -> None:
    parsed = parse_selection(text)
    assert not parsed.ok
    assert parsed.errors


def test_parse_keeps_good_tokens_next_to_bad_ones() -> None:
    parsed = parse_selection("1-3,oops,9")
    assert parsed.numbers == [1, 2, 3, 9]
    assert parsed.errors == ["'oops' is not a number or a range like 1-42"]


def test_parse_refuses_absurd_ranges() -> None:
    parsed = parse_selection(f"1-{MAX_SELECTION_SIZE + 1}")
    assert not parsed.ok
    assert parsed.numbers == []


def test_parse_strict_raises_invalid_selection() -> None:
    assert parse_selection_strict("1-2") == [1, 2]
    with pytest.raises(InvalidSelection) as excinfo:
        parse_selection_strict("1-2,bad")
    assert excinfo.value.slug == "invalid-selection"
    assert excinfo.value.unresolved == ["'bad' is not a number or a range like 1-42"]
    assert excinfo.value.extras == {"unresolved": excinfo.value.unresolved}


def test_resolve_canonical_example_yields_43_ids_and_counts_archived() -> None:
    candidates = selection_candidates(200, archived=[1, 2, 180])
    parsed = parse_selection("1-42,180")
    result = resolve_selection(candidates, numbers=parsed.numbers)
    assert len(result.resolved) == 43
    assert result.resolved[:3] == ["item0001", "item0002", "item0003"]
    assert result.resolved[-1] == "item0180"
    assert result.unresolved == []
    assert result.already_archived_count == 3
    assert result.numbering_used is Numbering.source
    assert result.to_archive == result.resolved


def test_resolve_reports_unresolved_numbers_in_request_order() -> None:
    candidates = selection_candidates(10)
    result = resolve_selection(candidates, numbers=[9, 10, 11, 12, 1])
    assert result.resolved == ["item0009", "item0010", "item0001"]
    assert result.unresolved == ["11", "12"]


def test_source_numbering_wins_and_falls_back_to_ordinal() -> None:
    # ordinal 1 carries source_number 101; ordinal 2 has none; ordinals 3 and 4 share 300.
    candidates = selection_candidates(4, source_numbers={1: 101, 2: None, 3: 300, 4: 300})
    result = resolve_selection(candidates, numbers=[101, 2, 300, 1])
    # 101 -> unique source number (ordinal 1); 2 -> ordinal fallback; 300 ambiguous -> ordinal
    # fallback fails (no ordinal 300); 1 -> no unique source number 1 -> ordinal 1 (already taken).
    assert result.resolved == ["item0001", "item0002"]
    assert result.unresolved == ["300"]


def test_ordinal_numbering_ignores_source_numbers() -> None:
    candidates = selection_candidates(3, source_numbers={1: 500, 2: 501, 3: 502})
    result = resolve_selection(candidates, numbers=[500, 2], numbering=Numbering.ordinal)
    assert result.resolved == ["item0002"]
    assert result.unresolved == ["500"]
    assert result.numbering_used is Numbering.ordinal


def test_explicit_item_ids_mix_with_numbers_and_dedupe() -> None:
    candidates = selection_candidates(5)
    result = resolve_selection(
        candidates, numbers=[2, 3], item_ids=["item0003", "item0005", "nope"]
    )
    assert result.resolved == ["item0002", "item0003", "item0005"]
    assert result.unresolved == ["nope"]


def test_resolve_with_nothing_requested() -> None:
    result = resolve_selection(selection_candidates(3))
    assert result.resolved == [] and result.unresolved == []
    assert result.already_archived_count == 0
