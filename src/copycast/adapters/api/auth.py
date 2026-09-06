"""HTTP Basic authentication: the operator pair everywhere, a feed's own pair on its routes.

Off unless ``settings.auth.password`` is set (ADR 0011). When on:

* :class:`OperatorAuthMiddleware` requires the operator pair on every request
  except the health checks, the public feed routes and the MCP mount (MCP is
  gated by API keys inside the fastmcp app, see ``adapters.mcp.auth``);
* :func:`require_feed_access` on the ``/feeds`` router accepts the operator
  pair or the pair of the very feed being fetched, so a podcast client holding
  one feed's credentials reaches nothing else.

Both answer 401 as problem+json with a ``Basic`` challenge, which is what makes
browsers, Overcast and AntennaPod prompt for the pair.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.deps import get_container
from copycast.adapters.api.problems import WWW_AUTHENTICATE, build_problem
from copycast.domain.credentials import constant_time_equals
from copycast.domain.exceptions import Unauthorized
from copycast.settings import AuthSettings

OPEN_PREFIXES: tuple[str, ...] = ("/healthz",)
"""Never gated: compose healthchecks and Kubernetes probes send no credentials."""
FEEDS_PREFIX = "/feeds/"
MCP_PATH = "/mcp"


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
    if credentials is None or auth.password is None:
        return False
    # Compare both halves so timing reveals nothing about which one was wrong.
    user_ok = constant_time_equals(credentials.username, auth.username)
    pass_ok = constant_time_equals(credentials.password, auth.password)
    return user_ok and pass_ok


def is_gated(path: str) -> bool:
    """Paths the operator middleware challenges (everything but health, feeds and MCP)."""
    if path.startswith(OPEN_PREFIXES):
        return False
    if path.startswith(FEEDS_PREFIX):
        return False
    return not (path == MCP_PATH or path.startswith(MCP_PATH + "/"))


def unauthorized_response(path: str | None, detail: str) -> JSONResponse:
    problem = build_problem(None, status=401, slug="unauthorized", detail=detail)
    body = problem.model_dump(mode="json", exclude_none=True)
    if path is not None:
        body["instance"] = path
    return JSONResponse(
        body,
        status_code=401,
        media_type="application/problem+json",
        headers={"WWW-Authenticate": WWW_AUTHENTICATE, "Cache-Control": "no-store"},
    )


class OperatorAuthMiddleware:
    """Pure ASGI: require the operator pair on every gated path while auth is on."""

    def __init__(self, app: ASGIApp, auth: AuthSettings) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.auth.enabled:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if not is_gated(path):
            await self.app(scope, receive, send)
            return
        credentials = parse_basic(Headers(scope=scope).get("authorization"))
        if not is_operator(self.auth, credentials):
            detail = (
                "wrong username or password"
                if credentials is not None
                else "this Copycast requires a username and password"
            )
            await unauthorized_response(path, detail)(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def require_feed_access(
    request: Request,
    container: Annotated[ApiContainer, Depends(get_container)],
    feed_id: str,
) -> None:
    """The operator pair or this feed's own pair; a no-op while auth is off."""
    auth = container.settings.auth
    if not auth.enabled:
        return
    credentials = parse_basic(request.headers.get("authorization"))
    if credentials is None:
        raise Unauthorized("this feed requires its username and password")
    if is_operator(auth, credentials):
        return
    async with container.uow_factory() as uow:
        feed = await uow.feeds.get(feed_id)
        username: str | None = feed.auth_username if feed is not None else None
        password: str | None = feed.auth_password if feed is not None else None
    if (
        username is not None
        and password is not None
        and constant_time_equals(credentials.username, username)
        and constant_time_equals(credentials.password, password)
    ):
        return
    raise Unauthorized("wrong username or password for this feed")


FeedAccess = Depends(require_feed_access)

__all__ = [
    "FEEDS_PREFIX",
    "MCP_PATH",
    "OPEN_PREFIXES",
    "BasicCredentials",
    "FeedAccess",
    "OperatorAuthMiddleware",
    "is_gated",
    "is_operator",
    "parse_basic",
    "require_feed_access",
    "unauthorized_response",
]
