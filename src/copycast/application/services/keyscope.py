"""Argument-level API key scope checks, enforced in the services for ``/api`` and MCP alike.

The route and the tool check a key's scope against the capability (``read`` for reads,
``write`` for writes, ``full`` for deletions). Some arguments of a write delete on a
schedule or tune the Engine, so they need a ``full`` key even though the capability
itself is a ``write``: a Rolling window or an expiring Ephemeral policy, a Mirror's
``engine_options`` and a Local feed's ``autoprune_days``. The services call
:func:`require_key_scope` before any write, so both surfaces refuse them alike.
"""

from __future__ import annotations

from copycast.application.models import BackfillRequest
from copycast.application.services.context import ServiceContext
from copycast.domain.credentials import scopes_granted
from copycast.domain.enums import BackfillMode, KeyScope, PrincipalVia
from copycast.domain.exceptions import Forbidden


def require_key_scope(ctx: ServiceContext, needed: KeyScope, what: str) -> None:
    """Refuse ``what`` unless the calling API key's scope covers ``needed``.

    A no-op unless the Principal is an API key (a session, open mode or SYSTEM).
    """
    principal = ctx.principal
    if principal.via is not PrincipalVia.api_key:
        return
    granted = principal.key_scope
    if granted is not None and needed.value in scopes_granted(granted):
        return
    have = granted.value if granted is not None else "none"
    raise Forbidden(
        f"{what} needs an API key with scope '{needed.value}'; this key has scope '{have}'"
    )


def deletes_on_schedule(backfill: BackfillRequest | None) -> bool:
    """A Rolling window, or an Ephemeral policy whose Episodes expire."""
    if backfill is None:
        return False
    return backfill.mode is BackfillMode.rolling or (
        backfill.mode is BackfillMode.automatic and backfill.retention_days is not None
    )


def require_backfill_scope(
    ctx: ServiceContext, backfill: BackfillRequest | None, what: str
) -> None:
    """``full`` for a policy that deletes on a schedule (Rolling, expiring Ephemeral)."""
    if backfill is not None and deletes_on_schedule(backfill):
        require_key_scope(ctx, KeyScope.full, f"{what} (backfill.mode {backfill.mode.value})")


def require_engine_options_scope(ctx: ServiceContext, given: bool, what: str) -> None:
    """``full`` to set or change a Mirror's ``engine_options``.

    The allowlist (``domain.engine_options``) already keeps commands, paths and egress
    out; the scope is the second fence, so a leaked ``write`` key cannot tune the Engine.
    """
    if given:
        require_key_scope(ctx, KeyScope.full, f"{what} (engine_options)")


__all__ = [
    "deletes_on_schedule",
    "require_backfill_scope",
    "require_engine_options_scope",
    "require_key_scope",
]
