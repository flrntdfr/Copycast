"""structlog configuration shared by the api and worker processes.

Every record carries the contextvars bound with :func:`bind_context`
(``process``, ``request_id``, ``job_id``, ``mirror_id``, ``inbox_id``).
Stdlib loggers (uvicorn, sqlalchemy, alembic, yt_dlp) are routed through the
same processor chain so both processes emit one format.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

import structlog
from structlog.typing import EventDict, Processor

from copycast.settings import Settings

LogFormat = Literal["console", "json"]

HEALTH_PREFIX = "/healthz/"

_NOISY_LOGGERS: dict[str, int] = {
    "sqlalchemy.engine": logging.WARNING,
    "sqlalchemy.pool": logging.WARNING,
    "alembic": logging.INFO,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "yt_dlp": logging.INFO,
    "uvicorn.error": logging.INFO,
    "uvicorn.access": logging.INFO,
}


class HealthAccessFilter(logging.Filter):
    """Drops uvicorn access-log lines for the health endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            for arg in args:
                if isinstance(arg, str) and HEALTH_PREFIX in arg:
                    return False
        return HEALTH_PREFIX not in record.getMessage()


def _drop_color_message(_: Any, __: str, event_dict: EventDict) -> EventDict:
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(
    settings: Settings | None = None,
    *,
    process: str,
    fmt: LogFormat | None = None,
    level: str | None = None,
) -> None:
    """Configure structlog and the stdlib root logger for one process.

    ``process`` ("api", "worker", "cli") is bound as a contextvar so it shows
    on every line, including those emitted by third-party stdlib loggers.
    """
    log_format: LogFormat = fmt or (settings.log_format if settings else "console")
    log_level = (level or (settings.log_level if settings else "INFO")).upper()

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message,
    ]
    renderer: Processor
    if log_format == "json":
        shared.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(log_level)

    for name, min_level in _NOISY_LOGGERS.items():
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(max(min_level, logging.getLevelNamesMapping()[log_level]))

    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthAccessFilter) for f in access.filters):
        access.addFilter(HealthAccessFilter())

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(process=process)


def bind_context(**values: Any) -> None:
    """Bind request/job identifiers for the current context (None values are dropped)."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in values.items() if v is not None})


def unbind_context(*keys: str) -> None:
    structlog.contextvars.unbind_contextvars(*keys)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name) if name else structlog.stdlib.get_logger()
