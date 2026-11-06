"""``GET /api/me``: the signed-in User, their role, auth mode and quota.

Every load of the web app asks it, so it also re-issues the session cookie: the
browser's copy slides like the server's (``auth.session_days``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response

from copycast.adapters.api.deps import ContainerDep, PrincipalDep, ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.adapters.api.session_cookie import read_session_token, set_session_cookie
from copycast.application.models import MeRead
from copycast.application.services.auth import IssuedSession
from copycast.domain.enums import PrincipalVia

router = APIRouter(tags=["users"])


@router.get(
    "/me",
    operation_id="get_me",
    openapi_extra={"x-capability": "get_me"},
    response_model=MeRead,
    responses=problem_responses(401),
    summary="The signed-in User (session only); re-issues the session cookie",
)
async def get_me(
    request: Request,
    response: Response,
    container: ContainerDep,
    principal: PrincipalDep,
    services: ServicesDep,
) -> MeRead:
    me = await services.get_me()
    token = read_session_token(request.headers, container.settings)
    if principal.via is PrincipalVia.session and token is not None:
        days = container.settings.auth.session_days
        issued = IssuedSession(token=token, expires_at=datetime.now(UTC) + timedelta(days=days))
        set_session_cookie(response, issued, container.settings)
    return me


__all__ = ["router"]
