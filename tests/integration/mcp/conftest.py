"""MCP suite fixtures: the in-memory fastmcp client, an RSS Source, a worker runner.

``mcp_client`` overrides the root fixture so the fastmcp ``Client`` context is
entered and left from one task (pytest-asyncio runs fixture setup and
teardown in separate tasks, which anyio's cancel scopes refuse).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastmcp import Client

from copycast.adapters.api.app import TaskScopedContext
from copycast.adapters.mcp.server import create_mcp
from copycast.app import Container
from copycast.settings import Settings
from copycast.worker.runner import Runner
from tests.integration.api.conftest import source as _source_fixture
from tests.integration.worker.conftest import Source

pytestmark = pytest.mark.integration

source = _source_fixture


@pytest.fixture
def runner(container: Container) -> Runner:
    return Runner(
        container,
        worker_id="mcp-test-worker",
        concurrency=2,
        listen=False,
        claim_tick=0.05,
        monitor_tick=0.05,
    )


@pytest.fixture
async def mcp_client(
    settings: Settings, container: Container, app: Any
) -> AsyncIterator[Client[Any]]:
    """An in-memory fastmcp client over ``create_mcp`` (the api lifespan runs alongside)."""
    server = create_mcp(settings, container)
    client: Client[Any] = Client(server)
    scope = TaskScopedContext(client)
    await scope.start()
    try:
        yield client
    finally:
        await scope.stop()


def structured(result: Any) -> dict[str, Any]:
    """The tool's structured content; fastmcp wraps non-object schemas (unions) in ``result``."""
    content: dict[str, Any] | None = result.structured_content
    assert content is not None, result
    if set(content) == {"result"} and isinstance(content["result"], dict):
        inner: dict[str, Any] = content["result"]
        return inner
    return content


__all__ = ["Source", "structured"]
