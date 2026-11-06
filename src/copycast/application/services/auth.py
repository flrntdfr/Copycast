"""Sign-in: login, logout, the signed-in User, and the session a cookie names.

The contract is fixed here; the bodies that need sessions, throttling and TOTP
arrive with the foundation (``login``, ``logout``, ``authenticate_session`` and
``sync_users`` raise ``NotImplementedError`` until then).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from copycast.application.capabilities import capability
from copycast.application.models import LoginRequest, MeRead
from copycast.application.services.context import ServiceContext
from copycast.application.services.quota import quota_status
from copycast.domain.exceptions import Unauthorized
from copycast.domain.users import Principal

LOCK_THRESHOLD: Final = 5
"""Failures before the first lock."""
LOCK_BASE_SECONDS: Final = 30
"""The first lock; it doubles with each further failure."""
LOCK_MAX_SECONDS: Final = 900
SESSION_TOUCH_SECONDS: Final = 60
"""A session's ``last_seen_at`` and ``expires_at`` are written at most once a minute."""


def lock_seconds(failed_count: int) -> int:
    """How long a username stays locked after its ``failed_count``-th failure (0: not locked).

    5 failures lock it for 30 s, each further one doubles that, up to 15 minutes.
    """
    if failed_count < LOCK_THRESHOLD:
        return 0
    exponent = min(failed_count - LOCK_THRESHOLD, 16)
    return min(LOCK_BASE_SECONDS * 2**exponent, LOCK_MAX_SECONDS)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session: its token goes into the cookie, never into a response body."""

    token: str
    expires_at: datetime


@capability("login", request=LoginRequest)
async def login(
    ctx: ServiceContext, body: LoginRequest, *, user_agent: str | None = None
) -> IssuedSession:
    """Check a username, password and TOTP code and open a session."""
    raise NotImplementedError("login arrives with the 3.0 foundation")


@capability("logout")
async def logout(ctx: ServiceContext, token: str | None) -> None:
    """Close the session ``token`` names, if any (idempotent)."""
    raise NotImplementedError("logout arrives with the 3.0 foundation")


@capability("get_me", response=MeRead)
async def get_me(ctx: ServiceContext) -> MeRead:
    """The signed-in User: name, role, auth mode, how they signed in, TOTP and quota."""
    principal = ctx.principal
    if principal.username is None:
        raise Unauthorized()
    user = ctx.users.get(principal.username)
    async with ctx.uow_factory() as uow:
        quota = await quota_status(uow, ctx.users, principal.username)
    return MeRead(
        username=principal.username,
        admin=principal.admin,
        auth_mode=ctx.users.mode,
        via=principal.via,
        totp=user is not None and user.totp_key is not None,
        quota=quota,
    )


@capability("authenticate_session")
async def authenticate_session(ctx: ServiceContext, token: str) -> Principal | None:
    """The Principal of a live session token (sliding its expiry), else None."""
    raise NotImplementedError("sessions arrive with the 3.0 foundation")


async def sync_users(ctx: ServiceContext) -> None:
    """Startup: the configured Users' rows, credential changes, stale sessions and attempts."""
    raise NotImplementedError("sessions arrive with the 3.0 foundation")


__all__ = [
    "LOCK_BASE_SECONDS",
    "LOCK_MAX_SECONDS",
    "LOCK_THRESHOLD",
    "SESSION_TOUCH_SECONDS",
    "IssuedSession",
    "authenticate_session",
    "get_me",
    "lock_seconds",
    "login",
    "logout",
    "sync_users",
]
