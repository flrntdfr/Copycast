"""Netscape cookie files: validation, the summary, and what never leaks."""

from __future__ import annotations

import pytest

from copycast.domain.cookies import (
    COOKIES_MAX_BYTES,
    InvalidCookies,
    parse_cookies,
    registrable_domain,
)

VALID = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.haxx.se/rfc/cookie_spec.html\n"
    "\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tsecret-value\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1790000000\tHSID\tanother\n"
    ".google.com\tTRUE\t/\tTRUE\t1790000000\tNID\tx\n"
    "soundcloud.com\tFALSE\t/\tFALSE\t0\toauth_token\ty\n"
)


def test_parse_summarises_without_keeping_values() -> None:
    summary = parse_cookies(VALID)
    assert summary.cookie_count == 4
    assert summary.domains == ["google.com", "soundcloud.com", "youtube.com"]
    assert summary.youtube is True
    assert "secret-value" not in repr(summary)


def test_parse_without_youtube() -> None:
    summary = parse_cookies("soundcloud.com\tFALSE\t/\tFALSE\t0\ttoken\ty\r\n")
    assert summary.cookie_count == 1 and summary.youtube is False


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no cookie lines"),
        ("# just a comment\n\n", "no cookie lines"),
        ("SID=abc; HSID=def", "tab-separated fields"),
        (".youtube.com\tTRUE\t/\tTRUE\tsoon\tSID\tv\n", "not a timestamp"),
        ("x" * (COOKIES_MAX_BYTES + 1), "larger than"),
    ],
)
def test_parse_rejects_what_is_not_a_cookie_file(text: str, message: str) -> None:
    with pytest.raises(InvalidCookies, match=message):
        parse_cookies(text)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (".www.youtube.com", "youtube.com"),
        ("YouTube.com", "youtube.com"),
        ("localhost", "localhost"),
    ],
)
def test_registrable_domain(host: str, expected: str) -> None:
    assert registrable_domain(host) == expected
