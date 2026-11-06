"""HTTP Basic on the published feeds: a feed's own pair (and, in legacy mode, the operator's).

The web UI and ``/api`` take sessions and API keys (``adapters.api.principal``);
``/feeds`` keeps HTTP Basic, which podcast apps speak. While authentication is on,
:func:`require_feed_access` on the ``/feeds`` router accepts the pair of the very feed
being fetched, so a podcast client holding one feed's credentials reaches nothing
else. The ``/feeds/{id}/k/{key}/`` media and asset routes also take the feed's media
key from the path: the enclosure URLs a feed publishes carry it, because a streaming
player (iOS AVPlayer) never answers a challenge. A feed's XML also opens to a browser
session of its Owner or of an Admin (the bucket page links it). In legacy mode the
operator pair opens every feed, as before 3.0; in users mode there is no operator
pair, and a Feed whose Owner is no longer configured opens to no pair and no key.

Every refusal is a 401 problem+json with a ``Basic`` challenge, which is what makes
Overcast and AntennaPod prompt for the pair, and one and the same answer
(:data:`FEED_UNAUTHORIZED`) for a missing pair, a wrong pair, another feed's pair, a
wrong media key and a feed, item or asset that does not exist: the routes are no
oracle of what this server mirrors.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.deps import get_container
from copycast.adapters.api.principal import principal_of
from copycast.adapters.api.problems import WWW_AUTHENTICATE, build_problem, challenges
from copycast.domain.credentials import constant_time_equals, feed_media_key
from copycast.domain.enums import AuthMode
from copycast.domain.exceptions import Unauthorized
from copycast.domain.urls import ART_PATH_PREFIX, COPYCAST_ARTWORK_PATH
from copycast.settings import AuthSettings

HEALTH_PATH = "/healthz"
"""Never gated: compose healthchecks and Kubernetes probes send no credentials."""
FEEDS_PREFIX = "/feeds/"
UPLOADS_PREFIX = "/uploads"
LOGO_PATH = COPYCAST_ARTWORK_PATH
ART_PREFIX = ART_PATH_PREFIX
MCP_PATH = "/mcp"
FEED_UNAUTHORIZED = "this feed requires its username and password"
"""The detail of every 401 under ``/feeds``: it never says what was wrong, or whether the
feed exists."""
_ABSENT_PASSWORD = "-" * 24
"""Compared against when the feed does not exist, so an unknown feed costs the same work."""


@dataclass(frozen=True, slots=True)
class BasicCredentials:
    username: str
    password: str


def parse_basic(header: str | None) -> BasicCredentials | None:
    """The pair inside ``Authorization: Basic <base64(user:pass)>``, else ``None``."""
    if not header:
        return None
    scheme, _, payload = header.strip().partition(" ")
    if scheme.lower() != "basic" or not payload.strip():
        return None
    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return BasicCredentials(username=username, password=password)


def is_operator(auth: AuthSettings, credentials: BasicCredentials | None) -> bool:
    """The legacy operator pair (``auth.username`` and ``auth.password``); never in users
    mode, which has no plaintext password to compare."""
    if credentials is None or auth.password is None or auth.mode is not AuthMode.legacy:
        return False
    # Compare both halves so timing reveals nothing about which one was wrong.
    user_ok = constant_time_equals(credentials.username, auth.username)
    pass_ok = constant_time_equals(credentials.password, auth.password)
    return user_ok and pass_ok


ROBOTS_PATH = "/robots.txt"


def is_public_artwork(path: str) -> bool:
    """Artwork that answers without credentials: ``/art/{token}`` and the Copycast logo.

    Podcast apps, the SPA and the Media Session fetch images bare. The token's MAC
    (``domain.credentials.artwork_mac``) makes ``/art/`` URLs unguessable; the old
    ``/feeds/{id}/assets/*.artwork.*`` path, computable from a Source URL, takes the
    feed's pair or media key like every other asset.
    """
    return path.startswith(ART_PREFIX + "/") or path == LOGO_PATH


def unauthorized_response(path: str | None, detail: str) -> JSONResponse:
    """A 401 problem+json; the ``Basic`` challenge only under ``/feeds/``."""
    problem = build_problem(None, status=401, slug="unauthorized", detail=detail)
    body = problem.model_dump(mode="json", exclude_none=True)
    if path is not None:
        body["instance"] = path
    headers = {"Cache-Control": "no-store"}
    if challenges(path):
        headers["WWW-Authenticate"] = WWW_AUTHENTICATE
    return JSONResponse(
        body, status_code=401, media_type="application/problem+json", headers=headers
    )


async def require_feed_access(
    request: Request,
    container: Annotated[ApiContainer, Depends(get_container)],
    feed_id: str,
) -> None:
    """This feed's own pair, (on a ``/k/{key}/`` route) its media key, (on its XML) a
    session of its Owner or an Admin, or in legacy mode the operator pair; a no-op while
    auth is off.

    A router dependency, so it runs before the endpoint looks anything up (the path
    parameters are plain strings: no validation can answer first). Every refusal is
    the same :data:`FEED_UNAUTHORIZED`, and an unknown feed is compared against a
    stand-in pair so it takes the same work as a known one.
    """
    auth = container.settings.auth
    if not auth.enabled:
        return
    # Read from the path only: a ``?key=`` query must never stand in for the pair.
    key: str | None = request.path_params.get("key")
    credentials = parse_basic(request.headers.get("authorization"))
    principal = principal_of(request.scope) if request.url.path.endswith(".xml") else None
    if credentials is None and key is None and principal is None:
        raise Unauthorized(FEED_UNAUTHORIZED)
    if is_operator(auth, credentials):
        return
    async with container.uow_factory() as uow:
        feed = await uow.feeds.get(feed_id)
        known = feed is not None and container.users.is_configured(feed.owner)
        owner: str | None = feed.owner if feed is not None else None
        username: str = feed.auth_username if feed is not None else ""
        password: str = feed.auth_password if feed is not None else _ABSENT_PASSWORD
    if known and principal is not None and (principal.admin or principal.username == owner):
        return
    key_ok = key is not None and constant_time_equals(key, feed_media_key(feed_id, password))
    pair_ok = False
    if credentials is not None:
        user_ok = constant_time_equals(credentials.username, username)
        pass_ok = constant_time_equals(credentials.password, password)
        pair_ok = user_ok and pass_ok
    if known and (key_ok or pair_ok):
        return
    raise Unauthorized(FEED_UNAUTHORIZED)


FeedAccess = Depends(require_feed_access)

__all__ = [
    "ART_PREFIX",
    "FEEDS_PREFIX",
    "FEED_UNAUTHORIZED",
    "HEALTH_PATH",
    "LOGO_PATH",
    "MCP_PATH",
    "ROBOTS_PATH",
    "BasicCredentials",
    "FeedAccess",
    "is_operator",
    "is_public_artwork",
    "parse_basic",
    "require_feed_access",
    "unauthorized_response",
]
