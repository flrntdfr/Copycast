"""Quota sizes, limits, states and the remaining budget."""

from __future__ import annotations

import pytest

from copycast.domain import quota as q


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (12345, 12345),
        ("12345", 12345),
        ("10 GB", 10_000_000_000),
        ("10GB", 10_000_000_000),
        ("500 MB", 500_000_000),
        ("1.5 TB", 1_500_000_000_000),
        ("2 kB", 2000),
        ("2 KB", 2000),
        ("7 B", 7),
        ("  3 gb ", 3_000_000_000),
    ],
)
def test_parse_size_accepts_decimal_units(value: str | int, expected: int) -> None:
    assert q.parse_size(value) == expected


@pytest.mark.parametrize("value", ["10 GiB", "ten GB", "", "GB", "-1 GB", "1,5 GB", -1, True])
def test_parse_size_refuses_everything_else(value: str | int) -> None:
    with pytest.raises(ValueError, match="10 GB"):
        q.parse_size(value)


def test_parse_quota_limit_and_bytes() -> None:
    assert q.parse_quota_limit("unlimited") == "unlimited"
    assert q.parse_quota_limit(" Unlimited ") == "unlimited"
    assert q.parse_quota_limit("10 GB") == 10_000_000_000
    assert q.parse_quota_limit(42) == 42
    assert q.quota_bytes("unlimited") is None
    assert q.quota_bytes(10) == 10
    with pytest.raises(ValueError):
        q.parse_quota_limit("lots")


@pytest.mark.parametrize(
    ("used", "quota", "state"),
    [
        (0, None, "ok"),
        (10**15, None, "ok"),
        (899, 1000, "ok"),
        (8999, 10000, "ok"),
        (900, 1000, "near"),
        (999, 1000, "near"),
        (1000, 1000, "over"),
        (1500, 1000, "over"),
        (0, 0, "over"),
    ],
)
def test_quota_state_thresholds(used: int, quota: int | None, state: str) -> None:
    assert q.quota_state(used, quota) == state


def test_quota_state_at_89_9_percent_is_ok() -> None:
    assert q.quota_state(899_000, 1_000_000) == "ok"
    assert q.quota_state(899_999, 1_000_000) == "ok"
    assert q.quota_state(900_000, 1_000_000) == "near"


def test_remaining_never_negative() -> None:
    assert q.remaining(10, None) is None
    assert q.remaining(400, 1000) == 600
    assert q.remaining(1500, 1000) == 0


@pytest.mark.parametrize(
    ("n", "text"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 kB"),
        (3_200_000_000, "3.2 GB"),
        (10_000_000_000, "10.0 GB"),
        (999_950, "1.0 MB"),
        (12_345_678, "12.3 MB"),
        (2_500_000_000_000, "2.5 TB"),
        (5_000_000_000_000_000, "5000.0 TB"),
    ],
)
def test_format_size(n: int, text: str) -> None:
    assert q.format_size(n) == text
