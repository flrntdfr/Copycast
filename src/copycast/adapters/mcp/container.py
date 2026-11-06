"""What the MCP server needs from the composition root: the services, as SYSTEM (for the
key verifier only) or as a Principal (every tool and resource)."""

from __future__ import annotations

from typing import Protocol

from copycast.application.services import Services
from copycast.application.users import UserDirectory
from copycast.domain.users import Principal


class ServicesProvider(Protocol):
    """``copycast.app.Container`` as the MCP tools see it."""

    @property
    def services(self) -> Services: ...
    @property
    def users(self) -> UserDirectory: ...
    def services_for(self, principal: Principal) -> Services: ...


__all__ = ["ServicesProvider"]
