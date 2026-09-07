"""``create_app(settings, container)``: routers, public routes, health, MCP mount, SPA.

Building the app touches neither the database nor the data directory
(``copycast openapi`` needs that); the lifespan runs the startup sequence:
layout version, schema (``COPYCAST_AUTO_MIGRATE``), default Inbox, EventHub,
the per-process probe cache and the FastMCP session manager, and undoes it on
shutdown. :func:`preflight` is the part ``copycast api`` runs before uvicorn
so a bad layout or schema exits 2 with one actionable line.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import PlainTextResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from copycast.adapters.api.auth import ROBOTS_PATH, OperatorAuthMiddleware
from copycast.adapters.api.cache import FeedCache
from copycast.adapters.api.container import ApiContainer
from copycast.adapters.api.events import EventHub, psycopg_conninfo
from copycast.adapters.api.health import create_health_router
from copycast.adapters.api.middleware import (
    NoRobotsMiddleware,
    NoStoreJsonMiddleware,
    RequestContextMiddleware,
)
from copycast.adapters.api.problems import install_exception_handlers, relabel_problem_content
from copycast.adapters.api.routes import create_api_router
from copycast.adapters.api.routes import public as public_routes
from copycast.adapters.api.spa import mount_spa
from copycast.adapters.mcp.server import create_mcp
from copycast.logging import get_logger
from copycast.settings import Settings, SettingsError
from copycast.version import APP_VERSION

API_PREFIX = "/api"
FEEDS_PREFIX = "/feeds"
HEALTH_PREFIX = "/healthz"
MCP_PREFIX = "/mcp"
GZIP_MINIMUM_SIZE = 500
GZIP_EXCLUDED_TYPES: tuple[str, ...] = (
    "audio/*",
    "video/*",
    "image/*",
    "font/*",
    "text/event-stream",
    "application/octet-stream",
    "application/zip",
    "application/gzip",
)
"""Everything else Copycast serves is XML, JSON, HTML, JS or CSS."""

log = get_logger(__name__)


class TaskScopedContext:
    """Enter an async context manager in its own task and exit it from that same task.

    The FastMCP session manager runs an anyio task group whose cancel scope
    must be left by the task that entered it; hosts (pytest-asyncio fixtures
    among them) do not promise to run lifespan startup and shutdown in one
    task, so the context lives in a task of its own.
    """

    def __init__(self, manager: AbstractAsyncContextManager[Any]) -> None:
        self._manager = manager
        self._started = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="copycast-mcp-lifespan")
        await self._started.wait()
        if self._error is not None:
            raise self._error

    async def _run(self) -> None:
        try:
            async with self._manager:
                self._started.set()
                await self._stop.wait()
        except BaseException as exc:
            self._error = exc
            self._started.set()
            raise

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class ExactMount:
    """Serve the exact ``prefix`` path from a sub-application mounted at ``prefix``.

    ``Mount("/mcp")`` only matches ``/mcp/...`` and answers ``/mcp`` with a
    307 to ``/mcp/``, which MCP clients do not follow on POST; this ASGI
    endpoint forwards ``/mcp`` into the sub-application as ``/`` instead.
    """

    def __init__(self, app: ASGIApp, prefix: str) -> None:
        self._app = app
        self._prefix = prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            child: Scope = dict(scope)
            root = str(child.get("root_path", "")) + self._prefix
            child["root_path"] = root
            child["path"] = root + "/"
            child["raw_path"] = (root + "/").encode("utf-8")
            await self._app(child, receive, send)
            return
        await self._app(scope, receive, send)


async def preflight(container: ApiContainer) -> None:
    """Layout version and schema policy; raises ``SettingsError`` (exit 2 in the CLI)."""
    container.ensure_layout()
    await container.prepare_schema()


ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


async def robots() -> PlainTextResponse:
    """Crawlers are told to stay out; the header on every response says the same."""
    return PlainTextResponse(ROBOTS_TXT, headers={"Cache-Control": "public, max-age=86400"})


def _lifespan(settings: Settings, container: ApiContainer, mcp_app: Any) -> Any:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        try:
            await preflight(container)
        except SettingsError as exc:
            log.error("api.startup_failed", error=str(exc))
            raise
        await container.services.ensure_default_inbox()
        _ = container.sources.cache  # the per-process probe cache
        hub = EventHub(psycopg_conninfo(settings.database_url))
        app.state.event_hub = hub
        await hub.start()
        mcp_lifespan = TaskScopedContext(mcp_app.router.lifespan_context(mcp_app))
        try:
            await mcp_lifespan.start()
            log.info("api.started", base_url=settings.base_url, bind=settings.bind)
            yield
        finally:
            await mcp_lifespan.stop()
            await hub.stop()
            container.close_http_client()
            await container.aclose()
            log.info("api.stopped")

    return lifespan


def create_app(settings: Settings, container: ApiContainer) -> FastAPI:
    mcp = create_mcp(settings, container)
    mcp_app = mcp.http_app(path="/", stateless_http=True, host_origin_protection=False)

    app = FastAPI(
        title="Copycast",
        version=APP_VERSION,
        description="Self-hosted podcast mirroring and archiving.",
        lifespan=_lifespan(settings, container, mcp_app),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.container = container
    app.state.mcp = mcp
    app.state.feed_cache = FeedCache()

    install_exception_handlers(app)
    # Innermost: runs right before routing, inside the request-id and gzip layers.
    app.add_middleware(OperatorAuthMiddleware, auth=settings.auth)
    app.add_middleware(NoStoreJsonMiddleware)
    app.add_middleware(NoRobotsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE, exclude_content_types=GZIP_EXCLUDED_TYPES
    )

    app.include_router(create_api_router(), prefix=API_PREFIX)
    app.include_router(public_routes.open_router, prefix=FEEDS_PREFIX)
    app.include_router(public_routes.router, prefix=FEEDS_PREFIX)
    app.add_api_route(ROBOTS_PATH, robots, methods=["GET"], include_in_schema=False)
    app.include_router(create_health_router(), prefix=HEALTH_PREFIX)
    app.router.routes.append(Route(MCP_PREFIX, ExactMount(mcp_app, MCP_PREFIX), name="mcp-root"))
    app.mount(MCP_PREFIX, mcp_app, name="mcp")
    if settings.web_dir is not None:
        mount_spa(app, settings.web_dir)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        document = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        app.openapi_schema = relabel_problem_content(document)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


__all__ = [
    "API_PREFIX",
    "FEEDS_PREFIX",
    "GZIP_EXCLUDED_TYPES",
    "HEALTH_PREFIX",
    "MCP_PREFIX",
    "ExactMount",
    "TaskScopedContext",
    "create_app",
    "preflight",
]
