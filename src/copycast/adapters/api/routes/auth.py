"""``POST /auth/login`` and ``POST /auth/logout``: browser sessions.

Outside ``/api``: signing in needs no session, and signing out answers 204 whether
or not a session came with the request. The session token travels in the cookie
only, never in a body. Both run as SYSTEM: the password (or the cookie) is their
credential, not a Principal.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from copycast.adapters.api.deps import ContainerDep, SystemServicesDep
from copycast.adapters.api.problems import problem_responses, slug_responses
from copycast.adapters.api.session_cookie import (
    clear_session_cookie,
    read_session_token,
    set_session_cookie,
)
from copycast.application.models import LoginRequest

router = APIRouter(tags=["users"])


@router.post(
    "/login",
    operation_id="login",
    openapi_extra={"x-capability": "login"},
    status_code=204,
    responses={
        **problem_responses(404, 422),
        **slug_responses(
            {400: "https-required", 401: "login-failed", 429: "login-locked", 503: "login-busy"}
        ),
    },
    summary="Sign in with a username, password and TOTP code; sets the session cookie",
)
async def login(
    request: Request, container: ContainerDep, services: SystemServicesDep, body: LoginRequest
) -> Response:
    settings = container.settings
    issued = await services.login(
        body,
        user_agent=request.headers.get("user-agent"),
        scheme=request.url.scheme,
        presented_token=read_session_token(request.headers, settings),
    )
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    set_session_cookie(response, issued, settings)
    return response


@router.post(
    "/logout",
    operation_id="logout",
    openapi_extra={"x-capability": "logout"},
    status_code=204,
    summary="Sign out: ends the session the cookie names, if any, and clears the cookie",
)
async def logout(
    request: Request, container: ContainerDep, services: SystemServicesDep
) -> Response:
    settings = container.settings
    await services.logout(read_session_token(request.headers, settings))
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    clear_session_cookie(response, settings)
    return response


__all__ = ["router"]
