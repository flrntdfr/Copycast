"""Who a request acts as: one pure ASGI middleware resolves its Principal.

* Open mode: the implicit Admin, on every path, with no database access.
* Otherwise a session cookie, on any path, resolves through ``authenticate_session``;
  when there is none (or it names no live session), ``Authorization: Bearer cck_...``
  resolves on ``/api/`` paths only, through ``authenticate_api_key``, to the key's
  owner with the key's id and scope (a key whose owner is no longer configured
  resolves to nothing).

The Principal (or None) goes into ``scope["state"]["principal"]``. ``/api`` answers a
request without one with 401 problem+json, with no ``Basic`` challenge (the SPA shows
its login page instead of the browser's prompt). Nothing else is gated here:
``/feeds`` checks its own credentials (``auth.require_feed_access``), ``/mcp`` its API
keys, ``/uploads`` and ``/art`` their tokens, and ``POST /auth/login`` and
``/auth/logout`` need no session.
"""

from __future__ import annotations

from typing import Final, cast

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.problems import PROBLEM_MEDIA_TYPE, build_problem
from copycast.adapters.api.session_cookie import read_session_token
from copycast.domain.credentials import looks_like_api_key
from copycast.domain.enums import AuthMode, PrincipalVia
from copycast.domain.users import Principal

PRINCIPAL_STATE_KEY: Final = "principal"
API_PREFIX: Final = "/api"
UNAUTHENTICATED_DETAIL: Final = "sign in to use Copycast"


def requires_principal(path: str) -> bool:
    """``/api`` and everything under ``/api/``."""
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def bearer_token(headers: Headers) -> str | None:
    """The token of ``Authorization: Bearer <token>``, else None."""
    header = headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def principal_of(scope: Scope) -> Principal | None:
    """The Principal the middleware resolved for this request, if any."""
    state = scope.get("state")
    if not isinstance(state, dict):
        return None
    value: object = cast(dict[str, object], state).get(PRINCIPAL_STATE_KEY)
    return value if isinstance(value, Principal) else None


def unauthenticated_response(path: str) -> JSONResponse:
    """401 problem+json without ``WWW-Authenticate``, never cached."""
    problem = build_problem(None, status=401, slug="unauthorized", detail=UNAUTHENTICATED_DETAIL)
    body = problem.model_dump(mode="json", exclude_none=True)
    body["instance"] = path
    return JSONResponse(
        body,
        status_code=401,
        media_type=PROBLEM_MEDIA_TYPE,
        headers={"Cache-Control": "no-store"},
    )


class PrincipalMiddleware:
    """Pure ASGI: resolve the request's Principal; refuse ``/api`` without one."""

    def __init__(self, app: ASGIApp, *, container: ApiContainer) -> None:
        self.app = app
        self.container = container

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        principal = await self.resolve(scope, path)
        state = scope.setdefault("state", {})
        state[PRINCIPAL_STATE_KEY] = principal
        if principal is None and requires_principal(path):
            await unauthenticated_response(path)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def resolve(self, scope: Scope, path: str) -> Principal | None:
        return await resolve_principal(self.container, Headers(scope=scope), path)


async def resolve_principal(
    container: ApiContainer, headers: Headers, path: str
) -> Principal | None:
    """The Principal a request's credentials resolve to now (see the module docstring);
    the event stream calls it again while open, so a signed-out session ends it."""
    users = container.users
    if users.mode is AuthMode.open:
        return users.open_principal()
    # The container's services act as SYSTEM: the capabilities that resolve
    # credentials are internal.
    services = container.services
    token = read_session_token(headers, container.settings)
    if token is not None:
        principal = await services.authenticate_session(token)
        if principal is not None:
            return principal
    if not path.startswith(API_PREFIX + "/"):
        return None
    secret = bearer_token(headers)
    if secret is None or not looks_like_api_key(secret):
        return None
    key = await services.authenticate_api_key(secret)
    if key is None:
        return None
    return users.principal(
        key.owner, via=PrincipalVia.api_key, api_key_id=key.id, key_scope=key.scope
    )


__all__ = [
    "PRINCIPAL_STATE_KEY",
    "PrincipalMiddleware",
    "bearer_token",
    "principal_of",
    "requires_principal",
    "resolve_principal",
    "unauthenticated_response",
]
