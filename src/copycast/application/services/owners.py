"""Whose a call is: the User new rows belong to and whose Cookie files a probe carries."""

from __future__ import annotations

from copycast.application.services.context import ServiceContext


def acting_owner(ctx: ServiceContext) -> str:
    """The Principal's username; for SYSTEM, the owner of existing data (startup, rebuild)."""
    return ctx.principal.username or ctx.users.owner_of_existing


__all__ = ["acting_owner"]
