"""RFC 9457 problem details: every API error is ``application/problem+json``.

``type`` is ``urn:copycast:problem:<slug>``; domain exceptions carry their slug,
status and extension members (``existing_feed_id``, ``candidates``,
``unresolved``, ``keys``...). One handler covers every ``DomainError``, one the
request validation errors, one Starlette's ``HTTPException`` and one the rest.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from copycast.application.models import Problem
from copycast.domain.exceptions import DomainError
from copycast.logging import get_logger

PROBLEM_MEDIA_TYPE = "application/problem+json"

TITLES: dict[str, str] = {
    "not-found": "Not found",
    "validation": "Validation failed",
    "feed-exists": "Mirror already exists",
    "candidates-ambiguous": "Several candidates found",
    "source-unsupported": "Source unsupported",
    "source-kind-change": "Source kind change refused",
    "invalid-selection": "Invalid selection",
    "engine-option-rejected": "Engine option rejected",
    "conflict": "Conflict",
    "engine-unavailable": "Engine unavailable",
    "internal": "Internal error",
    "method-not-allowed": "Method not allowed",
    "http-error": "Request failed",
}

STATUS_SLUGS: dict[int, str] = {
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    422: "validation",
    500: "internal",
    503: "engine-unavailable",
}

log = get_logger(__name__)


def problem_type(slug: str) -> str:
    return Problem.problem_type(slug)


def build_problem(
    request: Request | None,
    *,
    status: int,
    slug: str,
    detail: str | None = None,
    title: str | None = None,
    **extras: Any,
) -> Problem:
    instance = request.url.path if request is not None else None
    return Problem(
        type=problem_type(slug),
        title=title or TITLES.get(slug, slug.replace("-", " ").capitalize()),
        status=status,
        detail=detail,
        instance=instance,
        **extras,
    )


def problem_response(
    request: Request | None,
    *,
    status: int,
    slug: str,
    detail: str | None = None,
    title: str | None = None,
    headers: dict[str, str] | None = None,
    **extras: Any,
) -> JSONResponse:
    problem = build_problem(request, status=status, slug=slug, detail=detail, title=title, **extras)
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return problem_response(
        request, status=exc.status, slug=exc.slug, detail=str(exc), **exc.extras
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        entry: dict[str, Any] = {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": str(err.get("msg", "")).removeprefix("Value error, "),
            "type": err.get("type", "value_error"),
        }
        errors.append(entry)
    return problem_response(
        request,
        status=422,
        slug="validation",
        detail="request validation failed",
        errors=errors,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    slug = STATUS_SLUGS.get(exc.status_code, "http-error")
    detail = str(exc.detail) if exc.detail else None
    headers = dict(exc.headers) if exc.headers else None
    return problem_response(
        request, status=exc.status_code, slug=slug, detail=detail, headers=headers
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("api.unhandled", path=request.url.path, exc_info=exc)
    return problem_response(
        request, status=500, slug="internal", detail="an unexpected error occurred"
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries for the given statuses.

    FastAPI documents them under the route's JSON media type; ``app.openapi()``
    (see ``adapters.api.app``) rewrites those entries to ``application/problem+json``.
    """
    return {
        status: {"model": Problem, "description": TITLES[STATUS_SLUGS[status]]}
        for status in statuses
    }


def _is_problem_schema(schema: object) -> bool:
    if not isinstance(schema, dict):
        return False
    ref = cast(dict[str, object], schema).get("$ref")
    return isinstance(ref, str) and ref.endswith("/Problem")


def relabel_problem_content(document: dict[str, Any]) -> dict[str, Any]:
    """Rename ``application/json`` to problem+json wherever a response schema is ``Problem``."""
    paths = cast(dict[str, dict[str, object]], document.get("paths", {}))
    for path_item in paths.values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = cast(dict[str, object], operation).get("responses")
            if not isinstance(responses, dict):
                continue
            for response in cast(dict[str, object], responses).values():
                if not isinstance(response, dict):
                    continue
                content = cast(dict[str, object], response).get("content")
                if not isinstance(content, dict):
                    continue
                typed = cast(dict[str, object], content)
                json_entry = typed.get("application/json")
                if not isinstance(json_entry, dict):
                    continue
                if _is_problem_schema(cast(dict[str, object], json_entry).get("schema")):
                    typed[PROBLEM_MEDIA_TYPE] = typed.pop("application/json")
    return document


__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "STATUS_SLUGS",
    "TITLES",
    "build_problem",
    "install_exception_handlers",
    "problem_response",
    "problem_responses",
    "problem_type",
    "relabel_problem_content",
]
