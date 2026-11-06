"""MCP resources: ``copycast://about``, ``copycast://feeds``, ``copycast://feeds/{feed_id}``."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError

from copycast.adapters.mcp.auth import check_scope, current_principal
from copycast.adapters.mcp.container import ServicesProvider
from copycast.application.services import Services
from copycast.domain.enums import KeyScope
from copycast.domain.exceptions import DomainError

JSON = "application/json"


def register_resources(mcp: FastMCP[Any], container: ServicesProvider) -> None:
    """Every resource reads as the calling key's owner (a ``read`` key suffices); another
    User's Feed is not found."""

    def services(resource: str) -> Services:
        check_scope(KeyScope.read, resource)
        return container.services_for(current_principal(container))

    async def about() -> str:
        """Application and engine versions, layout version and totals."""
        report = await services("copycast://about").about()
        return report.model_dump_json()

    async def feeds() -> str:
        """Every Feed (Mirrors and Buckets) with its feed_url."""
        page = await services("copycast://feeds").list_feeds()
        return page.model_dump_json()

    async def feed(feed_id: str) -> str:
        """One Feed by id."""
        try:
            found = await services("copycast://feeds/{feed_id}").get_feed(feed_id)
        except DomainError as exc:
            raise ResourceError(f"{exc} [{exc.slug}]") from exc
        return found.model_dump_json()

    mcp.resource("copycast://about", name="about", mime_type=JSON)(about)
    mcp.resource("copycast://feeds", name="feeds", mime_type=JSON)(feeds)
    mcp.resource("copycast://feeds/{feed_id}", name="feed", mime_type=JSON)(feed)


__all__ = ["register_resources"]
