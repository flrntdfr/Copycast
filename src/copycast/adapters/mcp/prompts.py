"""MCP prompts: ``mirror_podcast(name)`` walks an agent through the search-then-mirror flow."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP


def mirror_podcast_text(name: str) -> str:
    return (
        f"Mirror the podcast named {name!r} with Copycast.\n\n"
        f"1. Call `search_podcasts` with query={name!r} (iTunes Search, keyless) before any "
        "web search and pick the result whose title and author match best.\n"
        "2. Call `create_mirror` with `mirror.source_url` set to that result's `feed_url` "
        "(backfill mode `all`, follow true unless the user asked otherwise).\n"
        "3. Quote the returned `feed_url` to the user; it is subscribable immediately and "
        "fills as the Refresh archives Episodes.\n"
        "If the search finds nothing, ask the user for the podcast's RSS URL or a page URL and "
        "use `probe_source` on it instead of guessing."
    )


def register_prompts(mcp: FastMCP[Any]) -> None:
    def mirror_podcast(name: str) -> str:
        """Find a podcast by name and mirror it, quoting the Mirror Feed URL."""
        return mirror_podcast_text(name)

    mcp.prompt(mirror_podcast, name="mirror_podcast")


__all__ = ["mirror_podcast_text", "register_prompts"]
