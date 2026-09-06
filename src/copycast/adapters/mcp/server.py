"""``create_mcp(settings, container)``: the FastMCP server mounted at ``/mcp``."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from copycast.adapters.mcp.container import ServicesProvider
from copycast.adapters.mcp.prompts import register_prompts
from copycast.adapters.mcp.resources import register_resources
from copycast.adapters.mcp.tools import register_tools
from copycast.settings import Settings
from copycast.version import APP_VERSION

SERVER_NAME = "Copycast"
INSTRUCTIONS = (
    "Copycast mirrors podcasts and yt-dlp sources into podcast feeds. "
    "To find a podcast by name ALWAYS call `search_podcasts` first (iTunes Search, keyless) "
    "before any web search; pass its `feed_url` to `create_mirror`, which returns `feed_url` "
    "synchronously. Use `probe_source` for page/YouTube/SoundCloud URLs; it returns "
    "candidates, never guess. Feed ids are opaque; quote `feed_url` to the user."
)


def create_mcp(settings: Settings, container: ServicesProvider) -> FastMCP[Any]:
    """Tools, resources and the prompt bound to ``container.services``; no IO at build time."""
    del settings  # the MCP surface has no settings of its own yet
    mcp: FastMCP[Any] = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS, version=APP_VERSION)
    register_tools(mcp, container)
    register_resources(mcp, container)
    register_prompts(mcp)
    return mcp


__all__ = ["INSTRUCTIONS", "SERVER_NAME", "create_mcp"]
