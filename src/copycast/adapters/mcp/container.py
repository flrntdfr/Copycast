"""What the MCP server needs from the composition root: the bound services."""

from __future__ import annotations

from typing import Protocol

from copycast.application.services import Services


class ServicesProvider(Protocol):
    """``copycast.app.Container`` as the MCP tools see it."""

    @property
    def services(self) -> Services: ...


__all__ = ["ServicesProvider"]
