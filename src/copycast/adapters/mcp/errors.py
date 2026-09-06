"""Domain exceptions become ``ToolError`` messages an agent can act on."""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.exceptions import ToolError

from copycast.domain.exceptions import DomainError


def tool_error(exc: DomainError) -> ToolError:
    """``<message> [<slug>]`` plus the extension members as one JSON line."""
    message = f"{exc} [{exc.slug}]"
    if exc.extras:
        message += "\n" + json.dumps(exc.extras, default=str, ensure_ascii=False)
    return ToolError(message)


def guarded[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Wrap a tool body so every ``DomainError`` surfaces as a ``ToolError``."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except DomainError as exc:
            raise tool_error(exc) from exc

    return wrapper


def refuse(message: str, **extras: Any) -> ToolError:
    text = message
    if extras:
        text += "\n" + json.dumps(extras, default=str, ensure_ascii=False)
    return ToolError(text)


__all__ = ["guarded", "refuse", "tool_error"]
