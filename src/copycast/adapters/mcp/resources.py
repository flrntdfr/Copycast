"""MCP resources: ``copycast://about``, ``copycast://feeds``, ``copycast://feeds/{feed_id}``."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError

from copycast.adapters.mcp.container import ServicesProvider
from copycast.domain.exceptions import DomainError

JSON = "application/json"


def register_resources(mcp: FastMCP[Any], container: ServicesProvider) -> None:
    async def about() -> str:
        """Application and engine versions, layout version and totals."""
        report = await container.services.about()
        return report.model_dump_json()

    async def feeds() -> str:
        """Every Feed (Mirrors and Inboxes) with its feed_url."""
        page = await container.services.list_feeds()
        return page.model_dump_json()

    async def feed(feed_id: str) -> str:
        """One Feed by id."""
        try:
            found = await container.services.get_feed(feed_id)
        except DomainError as exc:
            raise ResourceError(f"{exc} [{exc.slug}]") from exc
        return found.model_dump_json()

    mcp.resource("copycast://about", name="about", mime_type=JSON)(about)
    mcp.resource("copycast://feeds", name="feeds", mime_type=JSON)(feeds)
    mcp.resource("copycast://feeds/{feed_id}", name="feed", mime_type=JSON)(feed)


__all__ = ["register_resources"]
