"""Sign-in: login, logout, the signed-in User, and the session a cookie names.

A session is 32 random bytes in a cookie, stored as its SHA-256; it slides for
``auth.session_days`` (written at most once a minute). Logins are throttled per
username, known or not (5 failures, then a lock of 30 s doubling up to 15 minutes),
and answer an unknown name and a wrong password alike: one 401 body, one argon2
verify each (a dummy one for an unknown name, with the slowest configured
parameters). Every verify runs on :data:`HASH_LIMITER`, two at a time, with at most
:data:`HASH_QUEUE_MAX` logins waiting; past that a login is refused as busy before
any verify. Holding its slot, a login first claims its attempt (the lock check and the
failure count in one locked row, before the verify), so a burst of guesses, from any
number of processes, never verifies more of them than the lock allows. An unknown name
is counted under its HMAC (:func:`attempt_key`), never stored in clear. A TOTP code is
good once: the step it matched is claimed per User.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import anyio
import anyio.to_thread

from copycast.application.capabilities import capability
from copycast.application.models import LoginRequest, MeRead
from copycast.application.services.context import ServiceContext
from copycast.application.services.quota import quota_status
from copycast.application.users import UserDirectory
from copycast.domain.credentials import mint_session_token, new_instance_secret, session_digest
from copycast.domain.enums import AuthMode, PrincipalVia
from copycast.domain.exceptions import (
    HttpsRequired,
    LoginBusy,
    LoginFailed,
    LoginLocked,
    NotFound,
    Unauthorized,
)
from copycast.domain.totp import match_step
from copycast.domain.users import Principal
from copycast.logging import get_logger

log = get_logger(__name__)

LOCK_THRESHOLD: Final = 5
"""Failures before the first lock."""
LOCK_BASE_SECONDS: Final = 30
"""The first lock; it doubles with each further failure."""
LOCK_MAX_SECONDS: Final = 900
SESSION_TOUCH_SECONDS: Final = 60
"""A session's ``last_seen_at`` and ``expires_at`` are written at most once a minute."""
HASH_SLOTS: Final = 2
"""argon2 verifies running at once in a process (2 x 64 MiB at the default parameters)."""
HASH_QUEUE_MAX: Final = 16
"""Logins that may wait for a slot; one more is refused as busy."""
HASH_WAIT_SECONDS: Final = 5.0
"""How long a login waits for a slot before it is refused as busy."""
USER_AGENT_MAX_LEN: Final = 512
LOGIN_ATTEMPTS_SECRET: Final = "login-attempts"
"""The ``instance_secrets`` row unknown typed names are keyed with in ``login_attempts``."""
UNKNOWN_NAME_PREFIX: Final = "?"
"""Starts the ``login_attempts`` key of an unknown name; no username contains it."""
PRUNE_EVERY_SECONDS: Final = 600.0
"""How often a login also prunes stale ``login_attempts`` rows (startup prunes too)."""

HASH_LIMITER: Final = anyio.CapacityLimiter(HASH_SLOTS)
"""The password-hash slots: never the default thread limiter, which FastAPI's sync
dependencies and ``FileResponse`` share."""
_HASH_THREADS: Final = anyio.CapacityLimiter(HASH_SLOTS)
"""The worker threads the verifies run on, once they hold a :data:`HASH_LIMITER` slot."""


class _HashQueue:
    """How many logins are waiting for, or holding, a hash slot in this process."""

    pending: int = 0


class _Prune:
    """When this process last pruned ``login_attempts`` from a login (monotonic seconds)."""

    last: float | None = None


def lock_seconds(failed_count: int) -> int:
    """How long a username stays locked after its ``failed_count``-th failure (0: not locked).

    5 failures lock it for 30 s, each further one doubles that, up to 15 minutes.
    """
    if failed_count < LOCK_THRESHOLD:
        return 0
    exponent = min(failed_count - LOCK_THRESHOLD, 16)
    return min(LOCK_BASE_SECONDS * 2**exponent, LOCK_MAX_SECONDS)


def attempt_key(secret: bytes, typed: str) -> str:
    """The ``login_attempts`` key of a typed name that is no User.

    ``?`` and 32 hex digits of its HMAC-SHA256 under the instance secret: the name (perhaps
    a password typed into the wrong field) is never stored, yet every process counts it
    under one key, so an unknown name locks exactly like a known one.
    """
    mac = hmac.new(secret, typed.encode("utf-8"), hashlib.sha256).hexdigest()
    return UNKNOWN_NAME_PREFIX + mac[:32]


@asynccontextmanager
async def hash_slot() -> AsyncGenerator[None]:
    """Hold one :data:`HASH_LIMITER` slot for the block (a login's claim and verify).

    Raises ``LoginBusy`` before the block runs when :data:`HASH_QUEUE_MAX` logins are
    already waiting, or when no slot frees up within :data:`HASH_WAIT_SECONDS`.
    """
    if _HashQueue.pending >= HASH_SLOTS + HASH_QUEUE_MAX:
        raise LoginBusy()
    _HashQueue.pending += 1
    try:
        borrower = object()
        acquired = False
        with anyio.move_on_after(HASH_WAIT_SECONDS):
            await HASH_LIMITER.acquire_on_behalf_of(borrower)
            acquired = True
        if not acquired:
            if borrower in HASH_LIMITER.statistics().borrowers:  # granted as the wait ran out
                HASH_LIMITER.release_on_behalf_of(borrower)
            raise LoginBusy()
        try:
            yield
        finally:
            HASH_LIMITER.release_on_behalf_of(borrower)
    finally:
        _HashQueue.pending -= 1


async def _in_thread[T](fn: Callable[[], T]) -> T:
    """Run ``fn`` on a hash thread; the caller holds a :func:`hash_slot`."""
    return await anyio.to_thread.run_sync(fn, limiter=_HASH_THREADS)


async def run_hashing[T](fn: Callable[[], T]) -> T:
    """Run one argon2 computation on a :data:`HASH_LIMITER` slot, in a thread.

    Raises ``LoginBusy`` without running ``fn`` when :data:`HASH_QUEUE_MAX` logins are
    already waiting, or when no slot frees up within :data:`HASH_WAIT_SECONDS`.
    """
    async with hash_slot():
        return await _in_thread(fn)


def _prune_due() -> bool:
    """Whether this login should also prune stale attempts (every :data:`PRUNE_EVERY_SECONDS`)."""
    now = time.monotonic()
    if _Prune.last is not None and now - _Prune.last < PRUNE_EVERY_SECONDS:
        return False
    _Prune.last = now
    return True


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session: its token goes into the cookie, never into a response body."""

    token: str
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _verify(users: UserDirectory, username: str | None, password: str) -> bool:
    """One argon2 verify: against ``username``'s hash, or a dummy for an unknown name.

    Runs in a thread: in legacy mode the first call also hashes the operator password.
    """
    user = users.get(username) if username is not None else None
    if user is None or user.password_hash is None:
        users.hasher.dummy_verify(password)
        return False
    return users.hasher.verify(user.password_hash, password)


@capability("login", request=LoginRequest)
async def login(
    ctx: ServiceContext,
    body: LoginRequest,
    *,
    user_agent: str | None = None,
    scheme: str | None = None,
    presented_token: str | None = None,
) -> IssuedSession:
    """Check a username, password and TOTP code and open a session.

    ``scheme`` is the request's (``https-required`` when ``base_url`` is https and it is
    not); ``presented_token`` is the session cookie the request came with, deleted on
    success so a stale or injected cookie never outlives a login.
    """
    users = ctx.users
    if users.mode is AuthMode.open:
        raise NotFound("sign-in (authentication is off)")
    if users.https and scheme is not None and scheme != "https":
        raise HttpsRequired(ctx.settings.base_url)
    typed = body.username.strip().lower()
    username = users.login_username(typed)
    # The slot first: a busy answer counts nothing. Then the claim, atomic with the lock
    # check, so only as many attempts as the lock allows ever reach a verify.
    async with hash_slot():
        now = _now()
        async with ctx.uow_factory() as uow:
            if username is not None:
                key = username
            else:
                secret = await uow.instance_secrets.get_or_create(
                    LOGIN_ATTEMPTS_SECRET, new_instance_secret
                )
                key = attempt_key(secret, typed)
            claim = await uow.login_attempts.claim(key, now)
            if _prune_due():
                await uow.login_attempts.prune(now)
        if not claim.allowed:
            await _in_thread(lambda: users.hasher.dummy_verify(body.password))
            locked_until = claim.locked_until or now
            retry_after = max(1, math.ceil((locked_until - now).total_seconds()))
            raise LoginLocked(retry_after)
        ok = await _in_thread(lambda: _verify(users, username, body.password))

    user = users.get(username) if username is not None else None
    if ok and user is not None and users.admin_login_refused(user):
        log.warning("auth.login_refused", reason="admin_without_totp", username=user.username)
        ok = False
    if ok and user is not None and user.totp_key is not None:
        ok = await _claim_totp(ctx, user.username, user.totp_key, body.totp)
    if not ok or user is None:
        # Counted already, by the claim. A typed name that is no User is never logged:
        # it may be a password.
        log.info("auth.login_failed", username=username, locked=claim.locked_until is not None)
        raise LoginFailed()

    token = mint_session_token()
    now = _now()
    expires_at = now + timedelta(days=ctx.settings.auth.session_days)
    agent = user_agent[:USER_AGENT_MAX_LEN] if user_agent else None
    async with ctx.uow_factory() as uow:
        await uow.login_attempts.reset(key)
        if presented_token:
            await uow.sessions.delete(session_digest(presented_token))
        await uow.sessions.add(
            session_digest(token), user.username, now=now, expires_at=expires_at, user_agent=agent
        )
        await uow.users.touch_login(user.username, now)
        await uow.sessions.delete_expired(now)
    log.info("auth.login", username=user.username)
    return IssuedSession(token=token, expires_at=expires_at)


async def _claim_totp(ctx: ServiceContext, username: str, key: bytes, code: str | None) -> bool:
    """Whether ``code`` matches a step later than the last one used, which it then claims."""
    if not code:
        return False
    async with ctx.uow_factory() as uow:
        last_step = await uow.users.totp_last_step(username)
        step = match_step(key, code, now=time.time(), last_step=last_step)
        if step is None:
            return False
        return await uow.users.claim_totp_step(username, step)


@capability("logout")
async def logout(ctx: ServiceContext, token: str | None) -> None:
    """Close the session ``token`` names, if any (idempotent)."""
    if not token:
        return
    async with ctx.uow_factory() as uow:
        await uow.sessions.delete(session_digest(token))


@capability("get_me", response=MeRead)
async def get_me(ctx: ServiceContext) -> MeRead:
    """The signed-in User: name, role, auth mode, how they signed in, TOTP and quota."""
    principal = ctx.principal
    if principal.username is None:
        raise Unauthorized()
    async with ctx.uow_factory() as uow:
        quota = await quota_status(uow, ctx.users, principal.username)
    return MeRead(
        username=principal.username,
        admin=principal.admin,
        auth_mode=ctx.users.mode,
        via=principal.via,
        # Never through users.get(): in legacy mode that may hash the operator password,
        # and this runs on the event loop.
        totp=ctx.users.has_totp(principal.username),
        quota=quota,
    )


@capability("authenticate_session")
async def authenticate_session(ctx: ServiceContext, token: str) -> Principal | None:
    """The Principal of a live session token (sliding its expiry), else None.

    A session past its expiry, or of a User no longer configured, is deleted. The role
    comes from the configuration on every call, so a demotion applies at the restart.
    """
    if not token:
        return None
    digest = session_digest(token)
    now = _now()
    async with ctx.uow_factory() as uow:
        row = await uow.sessions.get(digest)
        if row is None:
            return None
        principal = ctx.users.principal(row.username, via=PrincipalVia.session)
        if row.expires_at <= now or principal is None:
            await uow.sessions.delete(digest)
            return None
        if now - row.last_seen_at >= timedelta(seconds=SESSION_TOUCH_SECONDS):
            expires_at = now + timedelta(days=ctx.settings.auth.session_days)
            await uow.sessions.touch(digest, now=now, expires_at=expires_at)
        return principal


async def sync_users(ctx: ServiceContext) -> None:
    """Startup: the configured Users' rows, credential changes, stale sessions and attempts.

    Inserts a row per configured User (never deletes one); signs out every session of a
    User whose password hash or TOTP secret changed since the last start (and forgets
    their last TOTP step: the digest cannot tell which half changed, and a reset only
    lets the current code count once more); deletes the sessions of Users no longer
    configured, expired sessions and stale login attempts. Then logs the directory's
    warnings and, outside open mode, one warning per Owner of Feeds who is not configured
    (a removed User, or the previous legacy owner after ``auth.username`` changed).

    The credential check runs on the hash limiter: in legacy mode the stored fingerprint
    is an argon2 hash of the operator password, checked by a verify, and the operator
    password is hashed here once, so no request ever hashes it on the event loop.
    """
    users = ctx.users
    configured = users.usernames()
    async with ctx.uow_factory() as uow:
        await uow.users.ensure(configured)
        stored = await uow.users.credential_digests()
    changed = await run_hashing(lambda: users.changed_credentials(stored))
    now = _now()
    async with ctx.uow_factory() as uow:
        for name, digest in changed.items():
            ended = await uow.sessions.delete_for_user(name)
            await uow.users.set_credential_digest(name, digest, reset_totp=True)
            if stored.get(name) is not None:
                log.info("auth.credentials_changed", username=name, sessions_ended=ended)
        removed = await uow.sessions.delete_for_users_not_in(configured)
        expired = await uow.sessions.delete_expired(now)
        pruned = await uow.login_attempts.prune(now)
        owners: dict[str, int] = {}
        if users.mode is not AuthMode.open:
            for feed in await uow.feeds.list():
                if not users.is_configured(feed.owner):
                    owners[feed.owner] = owners.get(feed.owner, 0) + 1
    log.info(
        "auth.users_synced",
        mode=users.mode.value,
        users=len(configured),
        removed_sessions=removed,
        expired_sessions=expired,
        pruned_attempts=pruned,
    )
    for warning in users.startup_warnings():
        log.warning(warning)
    for owner, count in sorted(owners.items()):
        log.warning(
            "auth.removed_owner_has_feeds",
            owner=owner,
            feeds=count,
            hint=_reassign_hint(users, owner),
        )


def _reassign_hint(users: UserDirectory, owner: str) -> str:
    """How an operator gives ``owner``'s Feeds a configured Owner again."""
    if users.mode is AuthMode.users:
        return f"configure the user, or run: copycast users reassign {owner} <new>"
    # Legacy: auth.username changed (or a password was set after an open-mode upgrade
    # with another name); every Feed pair, media key and API key of `owner` answers 401.
    (current,) = users.usernames()
    return f"set auth.username back to {owner}, or run: copycast users reassign {owner} {current}"


__all__ = [
    "HASH_LIMITER",
    "HASH_QUEUE_MAX",
    "HASH_SLOTS",
    "HASH_WAIT_SECONDS",
    "LOCK_BASE_SECONDS",
    "LOCK_MAX_SECONDS",
    "LOCK_THRESHOLD",
    "LOGIN_ATTEMPTS_SECRET",
    "PRUNE_EVERY_SECONDS",
    "SESSION_TOUCH_SECONDS",
    "UNKNOWN_NAME_PREFIX",
    "IssuedSession",
    "attempt_key",
    "authenticate_session",
    "get_me",
    "hash_slot",
    "lock_seconds",
    "login",
    "logout",
    "run_hashing",
    "sync_users",
]
