"""``/healthz/live`` and ``/healthz/ready``.

Readiness reports ``{status, checks{db, schema, data_dir, layout, worker_seen_at}}``
and answers 503 when degraded. ``worker_seen_at`` is reported (the About page
shows it) but does not degrade the api: the api keeps serving feeds while the
worker restarts. The worker serves the same shape on its own port with extra
``scheduler`` and ``ffmpeg`` checks.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from copycast.adapters.api.container import ApiContainer
from copycast.application.models import ReadyCheck, ReadyRead

LIVE_PATH = "/live"
READY_PATH = "/ready"
WORKER_STALE_SECONDS = 90.0
STATUS_CHECKS: tuple[str, ...] = ("db", "schema", "data_dir", "layout")
"""Checks that decide ``status``; the others are informational."""

ExtraCheck = Callable[[], Awaitable[ReadyCheck]]


async def readiness(
    container: ApiContainer,
    *,
    extra_checks: Mapping[str, ExtraCheck] | None = None,
    status_checks: tuple[str, ...] = STATUS_CHECKS,
) -> ReadyRead:
    checks: dict[str, ReadyCheck] = {}
    try:
        async with container.db_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = ReadyCheck(ok=True)
    except Exception as exc:  # any driver or network failure
        checks["db"] = ReadyCheck(ok=False, detail=str(exc)[:200])
    if checks["db"].ok:
        try:
            current = await container.schema_is_current()
            checks["schema"] = ReadyCheck(ok=current, detail=None if current else "not at head")
        except Exception as exc:
            checks["schema"] = ReadyCheck(ok=False, detail=str(exc)[:200])
    else:
        checks["schema"] = ReadyCheck(ok=False, detail="database unavailable")

    data_dir = container.settings.data_dir
    writable = data_dir.is_dir() and os.access(data_dir, os.W_OK)
    checks["data_dir"] = ReadyCheck(ok=writable, detail=str(data_dir))
    version = container.layout.read_version()
    expected = container.expected_layout_version
    checks["layout"] = ReadyCheck(ok=version == expected, detail=version or "missing")

    checks["worker_seen_at"] = (
        await _worker_seen_at(container)
        if checks["db"].ok
        else ReadyCheck(ok=False, detail="database unavailable")
    )
    for name, check in (extra_checks or {}).items():
        checks[name] = await check()
    degraded = any(not checks[name].ok for name in status_checks if name in checks)
    return ReadyRead(status="degraded" if degraded else "ok", checks=checks)


async def _worker_seen_at(container: ApiContainer) -> ReadyCheck:
    try:
        async with container.uow_factory() as uow:
            heartbeat = await uow.telemetry.latest_heartbeat()
    except Exception as exc:
        return ReadyCheck(ok=False, detail=str(exc)[:200])
    if heartbeat is None:
        return ReadyCheck(ok=False, detail="never")
    seen_at: datetime = heartbeat.seen_at
    age = (datetime.now(UTC) - seen_at).total_seconds()
    return ReadyCheck(ok=age <= WORKER_STALE_SECONDS, detail=seen_at.isoformat())


def create_health_router() -> APIRouter:
    router = APIRouter(tags=["health"])

    async def live() -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    async def ready(request: Request) -> JSONResponse:
        container: ApiContainer = request.app.state.container
        report = await readiness(container)
        return JSONResponse(
            report.model_dump(mode="json"),
            status_code=200 if report.status == "ok" else 503,
            headers={"Cache-Control": "no-store"},
        )

    router.add_api_route(LIVE_PATH, live, operation_id="health_live", include_in_schema=False)
    router.add_api_route(
        READY_PATH,
        ready,
        operation_id="health_ready",
        response_model=ReadyRead,
        responses={503: {"model": ReadyRead, "description": "Degraded"}},
    )
    return router


__all__ = [
    "LIVE_PATH",
    "READY_PATH",
    "STATUS_CHECKS",
    "WORKER_STALE_SECONDS",
    "create_health_router",
    "readiness",
]
