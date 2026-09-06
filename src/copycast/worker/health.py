"""The worker's health endpoints on ``COPYCAST_WORKER_PORT``.

``/healthz/live`` answers 200 while the process runs; ``/healthz/ready``
answers the plan's ``{status, checks{...}}`` document (503 when degraded) with
the api's checks (db, schema, data_dir, layout, worker_seen_at) plus
``scheduler`` and ``ffmpeg``. Served by uvicorn on the worker's event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import uvicorn
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from copycast.adapters.db.migrate import schema_is_current
from copycast.adapters.storage.layout import LAYOUT_VERSION, Layout
from copycast.app import Container
from copycast.application.models import ReadyCheck, ReadyRead
from copycast.logging import get_logger
from copycast.worker.constants import HEARTBEAT_SECONDS, SCHEDULER_TICK_SECONDS

LIVE_PATH = "/healthz/live"
READY_PATH = "/healthz/ready"
STATUS_CHECKS = ("db", "schema", "data_dir", "layout", "worker_seen_at", "scheduler")
"""Checks that decide ``status``; ``ffmpeg`` is reported but does not degrade readiness."""

log = get_logger(__name__)


@dataclass
class WorkerState:
    """What the health checks observe about this process."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: datetime | None = None
    scheduler_last_tick_at: datetime | None = None
    ffmpeg_version: str | None = None
    running_jobs: int = 0
    quarantined_feeds: list[str] = field(default_factory=list[str])
    storage_full_until: datetime | None = None


def _age_ok(at: datetime | None, *, within: float) -> tuple[bool, str | None]:
    if at is None:
        return False, "never"
    age = (datetime.now(UTC) - at).total_seconds()
    return age <= within, at.isoformat()


async def readiness(container: Container, state: WorkerState) -> ReadyRead:
    checks: dict[str, ReadyCheck] = {}
    try:
        async with container.db_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = ReadyCheck(ok=True)
    except Exception as exc:  # any driver or network failure
        checks["db"] = ReadyCheck(ok=False, detail=str(exc)[:200])
    if checks["db"].ok:
        try:
            current = await schema_is_current(container.db_engine)
            checks["schema"] = ReadyCheck(ok=current, detail=None if current else "not at head")
        except Exception as exc:
            checks["schema"] = ReadyCheck(ok=False, detail=str(exc)[:200])
    else:
        checks["schema"] = ReadyCheck(ok=False, detail="database unavailable")
    data_dir = container.settings.data_dir
    writable = data_dir.is_dir() and os.access(data_dir, os.W_OK)
    checks["data_dir"] = ReadyCheck(ok=writable, detail=str(data_dir))
    version = Layout(data_dir).read_version()
    checks["layout"] = ReadyCheck(ok=version == LAYOUT_VERSION, detail=version)
    ok, detail = _age_ok(state.last_heartbeat_at, within=3 * HEARTBEAT_SECONDS)
    checks["worker_seen_at"] = ReadyCheck(ok=ok, detail=detail)
    ok, detail = _age_ok(state.scheduler_last_tick_at, within=3 * SCHEDULER_TICK_SECONDS)
    checks["scheduler"] = ReadyCheck(ok=ok, detail=detail)
    checks["ffmpeg"] = ReadyCheck(
        ok=state.ffmpeg_version is not None, detail=state.ffmpeg_version or "not found"
    )
    degraded = any(not checks[name].ok for name in STATUS_CHECKS)
    return ReadyRead(status="degraded" if degraded else "ok", checks=checks)


def create_health_app(container: Container, state: WorkerState) -> Starlette:
    async def live(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    async def ready(_: Request) -> JSONResponse:
        report = await readiness(container, state)
        return JSONResponse(
            report.model_dump(mode="json"),
            status_code=200 if report.status == "ok" else 503,
            headers={"Cache-Control": "no-store"},
        )

    return Starlette(routes=[Route(LIVE_PATH, live), Route(READY_PATH, ready)])


class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server that leaves the process signal handlers to the worker."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


class HealthServer:
    """uvicorn on the current loop without touching the process signal handlers."""

    def __init__(self, app: Any, *, host: str, port: int) -> None:
        config = uvicorn.Config(app, host=host, port=port, log_config=None, access_log=False)
        self._server = _EmbeddedServer(config)

    async def serve(self) -> None:
        await self._server.serve()

    def stop(self) -> None:
        self._server.should_exit = True

    @property
    def started(self) -> bool:
        return bool(self._server.started)

    async def wait_started(self, deadline_seconds: float = 10.0) -> bool:
        deadline = asyncio.get_running_loop().time() + deadline_seconds
        while not self.started and asyncio.get_running_loop().time() < deadline:
            if self._server.should_exit:
                return False
            await asyncio.sleep(0.05)
        return self.started


__all__ = [
    "LIVE_PATH",
    "READY_PATH",
    "STATUS_CHECKS",
    "HealthServer",
    "WorkerState",
    "create_health_app",
    "readiness",
]
