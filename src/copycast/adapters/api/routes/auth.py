"""``POST /auth/login`` and ``POST /auth/logout``: browser sessions.

Outside ``/api``: signing in needs no session, and signing out answers 204 whether
or not a session came with the request. The session token travels in the cookie
only, never in a body.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses, slug_responses
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
async def login(request: Request, services: ServicesDep, body: LoginRequest) -> Response:
    raise NotImplementedError("POST /auth/login arrives with the 3.0 foundation")


@router.post(
    "/logout",
    operation_id="logout",
    openapi_extra={"x-capability": "logout"},
    status_code=204,
    summary="Sign out: ends the session the cookie names, if any, and clears the cookie",
)
async def logout(request: Request, services: ServicesDep) -> Response:
    raise NotImplementedError("POST /auth/logout arrives with the 3.0 foundation")


__all__ = ["router"]
