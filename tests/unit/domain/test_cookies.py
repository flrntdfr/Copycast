"""Netscape cookie files: validation, the summary, and what never leaks."""

from __future__ import annotations

import pytest

from copycast.domain.cookies import (
    COOKIE_SERVICES,
    COOKIES_MAX_BYTES,
    InvalidCookies,
    cookie_file_name,
    cookie_lines,
    merge_cookie_files,
    parse_cookies,
    registrable_domain,
    services_for,
    split_cookie_jar,
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


# --------------------------------------------------------------------------- several files

YOUTUBE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\told\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1790000000\tHSID\tkeep\n"
)
TWITCH = ".twitch.tv\tTRUE\t/\tTRUE\t1790000000\tauth-token\tt\n"


def test_services_follow_the_domains_in_display_order() -> None:
    assert services_for(["vimeo.com", "google.com"]) == ["youtube", "vimeo"]
    assert services_for(["soundcloud.com"]) == []
    assert [service.id for service in COOKIE_SERVICES] == ["youtube", "twitch", "vimeo", "mixcloud"]


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("YouTube cookies.txt", "youtube-cookies"),
        ("/Users/me/Downloads/www.twitch.tv_cookies.txt", "www.twitch.tv_cookies"),
        ("  vimeo  ", "vimeo"),
    ],
)
def test_cookie_file_names_are_safe(raw: str, name: str) -> None:
    assert cookie_file_name(raw) == name


def test_a_cookie_file_needs_a_name() -> None:
    with pytest.raises(InvalidCookies):
        cookie_file_name("../.txt")


def test_merge_keeps_every_files_cookies_and_drops_comments() -> None:
    jar = merge_cookie_files({"youtube": YOUTUBE, "twitch": TWITCH})
    assert jar.startswith("# Netscape HTTP Cookie File\n")
    assert [key for key, _ in cookie_lines(jar)] == [
        (".twitch.tv", "/", "auth-token"),
        (".youtube.com", "/", "SID"),
        (".youtube.com", "/", "HSID"),
    ]
    assert parse_cookies(jar).cookie_count == 3


def test_split_carries_rotated_new_and_dropped_cookies_back_to_their_files() -> None:
    files = {"youtube": YOUTUBE, "twitch": TWITCH}
    jar = merge_cookie_files(files)
    # The site rotated SID, dropped HSID, and set a new youtube.com cookie and a stray one.
    jar = jar.replace("SID\told", "SID\trotated")
    jar = "\n".join(line for line in jar.splitlines() if "HSID" not in line)
    jar += "\n.youtube.com\tTRUE\t/\tTRUE\t1790000000\tPREF\tnew\n"
    jar += "example.org\tFALSE\t/\tFALSE\t0\tstray\tx\n"
    split = split_cookie_jar(jar, files)
    assert [line for _, line in cookie_lines(split["youtube"])] == [
        ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\trotated",
        ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tPREF\tnew",
    ]
    assert [line for _, line in cookie_lines(split["twitch"])] == [TWITCH.strip()]
    assert "stray" not in split["youtube"] + split["twitch"]
