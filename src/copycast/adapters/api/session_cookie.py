"""The session cookie: its name, flags and how a request presents it.

On an https site (``base_url``'s scheme, never the request's) the cookie is
``__Host-copycast_session`` with ``Secure``: the ``__Host-`` prefix pins it to this
host, path ``/``, and https. On plain http (a laptop, a tailnet without TLS) it is
``copycast_session`` without ``Secure``, which the browser would otherwise refuse to
store. Both are ``HttpOnly`` and ``SameSite=Lax`` and live ``auth.session_days``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import Response

from copycast.application.services.auth import IssuedSession
from copycast.settings import Settings

SECURE_COOKIE_NAME: Final = "__Host-copycast_session"
PLAIN_COOKIE_NAME: Final = "copycast_session"
COOKIE_PATH: Final = "/"
SAME_SITE: Final = "lax"


def is_https(settings: Settings) -> bool:
    return urlsplit(settings.base_url).scheme == "https"


def cookie_name(settings: Settings) -> str:
    """``__Host-copycast_session`` on https, else ``copycast_session``."""
    return SECURE_COOKIE_NAME if is_https(settings) else PLAIN_COOKIE_NAME


def set_session_cookie(response: Response, issued: IssuedSession, settings: Settings) -> None:
    """``Path=/; HttpOnly; SameSite=Lax; Max-Age=session_days``, plus ``Secure`` on https."""
    max_age = settings.auth.session_days * 86_400
    remaining = int((issued.expires_at - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        cookie_name(settings),
        issued.token,
        max_age=max(0, min(max_age, remaining)),
        path=COOKIE_PATH,
        secure=is_https(settings),
        httponly=True,
        samesite=SAME_SITE,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Expire the cookie in the browser (same name, path and flags it was set with)."""
    response.delete_cookie(
        cookie_name(settings),
        path=COOKIE_PATH,
        secure=is_https(settings),
        httponly=True,
        samesite=SAME_SITE,
    )


def read_session_token(headers: Headers, settings: Settings) -> str | None:
    """The token of the session cookie the request carries, if any."""
    raw = headers.get("cookie")
    if not raw:
        return None
    token = cookie_parser(raw).get(cookie_name(settings), "").strip()
    return token or None


__all__ = [
    "PLAIN_COOKIE_NAME",
    "SECURE_COOKIE_NAME",
    "clear_session_cookie",
    "cookie_name",
    "is_https",
    "read_session_token",
    "set_session_cookie",
]
