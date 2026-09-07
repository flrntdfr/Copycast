"""Netscape ``cookies.txt`` files, the format yt-dlp (and every browser exporter) speaks.

Copycast stores one such file for the Engine so sites that demand a login,
YouTube first of all, accept downloads from a server's IP. This module only
validates and summarises the text; it never keeps a cookie value.
"""

from __future__ import annotations

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
    "YOUTUBE_DOMAINS",
    "CookieSummary",
    "InvalidCookies",
    "parse_cookies",
    "registrable_domain",
]
