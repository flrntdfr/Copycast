"""Netscape ``cookies.txt`` files, the format yt-dlp (and every browser exporter) speaks.

Copycast stores such files for the Engine so sites that demand a login,
YouTube first of all, accept downloads from a server's IP. This module validates,
summarises, merges and splits the text; it never keeps a cookie value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from copycast.domain.exceptions import DomainError

COOKIES_MAX_BYTES: Final = 1024 * 1024
COOKIE_FIELDS: Final = 7
HTTPONLY_PREFIX: Final = "#HttpOnly_"
YOUTUBE_DOMAINS: Final = frozenset({"youtube.com", "google.com"})


class InvalidCookies(DomainError):
    """The text is not a Netscape cookie file (422 invalid-cookies)."""

    slug = "invalid-cookies"
    status = 422


@dataclass(frozen=True, slots=True)
class CookieSummary:
    """What the UI shows about a stored cookie file: never a value, only where it applies."""

    cookie_count: int
    domains: list[str] = field(default_factory=list[str])

    @property
    def youtube(self) -> bool:
        return any(domain in YOUTUBE_DOMAINS for domain in self.domains)


def registrable_domain(host: str) -> str:
    """``.www.youtube.com`` -> ``youtube.com``; two labels are enough for the summary."""
    labels = [label for label in host.lower().strip().strip(".").split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


@dataclass(frozen=True, slots=True)
class CookieService:
    """A site the Settings page shows as unlocked once a stored cookie file covers it."""

    id: str
    name: str
    domains: frozenset[str]


COOKIE_SERVICES: Final[tuple[CookieService, ...]] = (
    CookieService("youtube", "YouTube", YOUTUBE_DOMAINS),
    CookieService("twitch", "Twitch", frozenset({"twitch.tv"})),
    CookieService("vimeo", "Vimeo", frozenset({"vimeo.com"})),
    CookieService("mixcloud", "Mixcloud", frozenset({"mixcloud.com"})),
)


def services_for(domains: Iterable[str]) -> list[str]:
    """The ids of the services cookies for ``domains`` unlock, in display order."""
    covered = set(domains)
    return [service.id for service in COOKIE_SERVICES if covered & service.domains]


# --------------------------------------------------------------------------- several files

NETSCAPE_HEADER: Final = "# Netscape HTTP Cookie File"
COOKIE_FILE_NAME_MAX: Final = 64

type CookieKey = tuple[str, str, str]
"""What identifies a cookie in a jar: its domain, path and name."""


def cookie_file_name(raw: str) -> str:
    """A stored file's name from an upload's (``YouTube cookies.txt`` -> ``youtube-cookies``)."""
    stem = raw.strip().rsplit("/", 1)[-1].removesuffix(".txt").lower()
    name = re.sub(r"[^a-z0-9._-]+", "-", stem).strip("-._")[:COOKIE_FILE_NAME_MAX]
    if not name:
        raise InvalidCookies("give the cookie file a name made of letters or digits")
    return name


def cookie_lines(text: str) -> list[tuple[CookieKey, str]]:
    """Every cookie line of a Netscape file with its key; comments and blanks are dropped."""
    lines: list[tuple[CookieKey, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or (line.startswith("#") and not line.startswith(HTTPONLY_PREFIX)):
            continue
        fields = line.removeprefix(HTTPONLY_PREFIX).split("\t")
        if len(fields) == COOKIE_FIELDS:
            lines.append(((fields[0].lower(), fields[2], fields[5]), line))
    return lines


def _render(lines: Iterable[str]) -> str:
    return "\n".join([NETSCAPE_HEADER, "", *lines]) + "\n"


def merge_cookie_files(files: Mapping[str, str]) -> str:
    """One jar for the Engine: every file's cookies in name order (a later file wins a key)."""
    return _render(line for name in sorted(files) for _, line in cookie_lines(files[name]))


def split_cookie_jar(jar: str, files: Mapping[str, str]) -> dict[str, str]:
    """Carry what the Engine changed in the merged ``jar`` back into the files it came from.

    Sites rotate cookies and yt-dlp writes them back into the jar. Each file gets the jar's
    current line for every key it holds, loses the keys the jar dropped, and a key new to
    the jar goes to the first file (by name) holding cookies of the same domain.
    """
    current = dict(cookie_lines(jar))
    split: dict[str, list[str]] = {}
    claimed: set[CookieKey] = set()
    for name in sorted(files):
        keys = [key for key, _ in cookie_lines(files[name])]
        split[name] = [current[key] for key in keys if key in current]
        claimed.update(keys)
    owners = {
        registrable_domain(key[0]): name
        for name in sorted(files, reverse=True)
        for key, _ in cookie_lines(files[name])
    }
    for key, line in current.items():
        owner = owners.get(registrable_domain(key[0]))
        if key not in claimed and owner is not None:
            split[owner].append(line)
    return {name: _render(lines) for name, lines in split.items()}


def parse_cookies(text: str) -> CookieSummary:
    """Validate a Netscape cookie file and summarise it; raises :class:`InvalidCookies`."""
    if len(text.encode("utf-8")) > COOKIES_MAX_BYTES:
        raise InvalidCookies(f"the cookie file is larger than {COOKIES_MAX_BYTES // 1024} KiB")
    count = 0
    domains: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#") and not line.startswith(HTTPONLY_PREFIX):
            continue
        body = line.removeprefix(HTTPONLY_PREFIX) if line.startswith(HTTPONLY_PREFIX) else line
        fields = body.split("\t")
        if len(fields) != COOKIE_FIELDS:
            raise InvalidCookies(
                f"line {number} has {len(fields)} tab-separated fields, a cookie line has "
                f"{COOKIE_FIELDS}; export with a 'Get cookies.txt' browser extension"
            )
        if fields[4] and not fields[4].isdigit():
            raise InvalidCookies(f"line {number}: the expiry {fields[4]!r} is not a timestamp")
        count += 1
        domains.add(registrable_domain(fields[0]))
    if count == 0:
        raise InvalidCookies("no cookie lines found; the file is empty or only comments")
    return CookieSummary(cookie_count=count, domains=sorted(domains))


__all__ = [
    "COOKIES_MAX_BYTES",
    "COOKIE_FIELDS",
    "COOKIE_FILE_NAME_MAX",
    "COOKIE_SERVICES",
    "NETSCAPE_HEADER",
    "YOUTUBE_DOMAINS",
    "CookieKey",
    "CookieService",
    "CookieSummary",
    "InvalidCookies",
    "cookie_file_name",
    "cookie_lines",
    "merge_cookie_files",
    "parse_cookies",
    "registrable_domain",
    "services_for",
    "split_cookie_jar",
]
