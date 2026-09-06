"""The web UI: hashed assets under ``/assets`` (immutable) and ``index.html`` for the rest.

Mounted only when ``COPYCAST_WEB_DIR`` is set. The catch-all never shadows
``/api``, ``/feeds``, ``/mcp``, ``/healthz`` or ``/assets``: unknown paths under
those prefixes answer 404 instead of the UI shell.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

from copycast.adapters.api.problems import problem_response

RESERVED_PREFIXES = ("api", "feeds", "mcp", "healthz", "assets")
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"


class ImmutableStaticFiles(StaticFiles):
    """Vite's hashed bundles never change under the same name: cache them for a year."""

    def file_response(self, *args: object, **kwargs: object) -> Response:
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = IMMUTABLE_CACHE
        return response


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def mount_spa(app: FastAPI, web_dir: Path) -> None:
    root = web_dir.resolve()
    index = root / "index.html"
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", ImmutableStaticFiles(directory=str(assets)), name="spa-assets")

    async def spa(request: Request, path: str) -> Response:
        first = path.split("/", 1)[0]
        if first in RESERVED_PREFIXES:
            return problem_response(request, status=404, slug="not-found", detail="no such route")
        candidate = root / path if path else index
        if path and candidate.is_file() and _within(root, candidate):
            return FileResponse(candidate, headers={"Cache-Control": NO_CACHE})
        if not index.is_file():
            return problem_response(
                request, status=404, slug="not-found", detail="the web UI is not built"
            )
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": NO_CACHE})

    app.add_api_route(
        "/{path:path}", spa, methods=["GET", "HEAD"], include_in_schema=False, name="spa"
    )


__all__ = ["IMMUTABLE_CACHE", "NO_CACHE", "RESERVED_PREFIXES", "ImmutableStaticFiles", "mount_spa"]
