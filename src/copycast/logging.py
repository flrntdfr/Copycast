"""structlog configuration shared by the api and worker processes.

Every record carries the contextvars bound with :func:`bind_context`
(``process``, ``request_id``, ``job_id``, ``mirror_id``, ``bucket_id``).
Stdlib loggers (uvicorn, sqlalchemy, alembic, yt_dlp) are routed through the
same processor chain so both processes emit one format. The request log is
Copycast's own ``http.request`` line (``adapters.api.middleware``); uvicorn's
access log stays off.

Secrets never reach a line: :func:`redact_secrets` masks every credential whose
shape Copycast knows in every event value (nested containers included), last of
the shared chain so stdlib records and JSON tracebacks pass through it. The
console renderer formats tracebacks itself, so its output goes through
:func:`redact_text` once more; it prints plain tracebacks, without the locals of
each frame (a secret in a variable has no shape to match) and without wrapping
(which would split a URL). A JSON line is never re-redacted: the rules would
match inside its escapes and break the JSON.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any, Literal

import structlog
from structlog.typing import EventDict, Processor

from copycast.settings import Settings

LogFormat = Literal["console", "json"]

_NOISY_LOGGERS: dict[str, int] = {
    "sqlalchemy.engine": logging.WARNING,
    "sqlalchemy.pool": logging.WARNING,
    "alembic": logging.INFO,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "yt_dlp": logging.INFO,
    "uvicorn.error": logging.INFO,
}


REDACTED = "***"
MAX_SCHEME = 32
"""The longest URL scheme the userinfo rule recognises (registered schemes are far shorter)."""

_SEGMENT = r"[^/\s?#\"'<>]+"
_TOKEN = r"[A-Za-z0-9._~+/-]"
_CREDENTIAL_SYMBOL = re.compile(r"[0-9+/=_~-]")
BEARER_MIN_LETTERS = 16
"""A Bearer value of letters only is taken for a credential from this length on (prose
after "bearer" is shorter); a Basic one only when it decodes to a ``user:pass`` pair."""


def _is_basic_pair(value: str) -> bool:
    """``value`` is base64 of printable text holding a ``:``, as a Basic credential is."""
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
        text = decoded.decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    return ":" in text and text.isprintable()


def _scheme_credential(match: re.Match[str]) -> str:
    scheme, value = match.group(1), match.group(2)
    if (
        _CREDENTIAL_SYMBOL.search(value)
        or (scheme.lower() == "bearer" and len(value) >= BEARER_MIN_LETTERS)
        or (scheme.lower() == "basic" and _is_basic_pair(value))
    ):
        return f"{scheme} {REDACTED}"
    return match.group(0)


_REDACTIONS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # An Authorization header written out as text, whatever its scheme (first, so the
    # Basic/Bearer rule below never sees the scheme it already took). A Digest value is a
    # list of quoted parameters: the rest of the line goes.
    (
        re.compile(
            r"(?i)\b((?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?)"
            r"(?:digest\s+[^\r\n]*|(?:(?:basic|bearer|token)\s+)?[^\s\"',;}]+)"
        ),
        rf"\g<1>{REDACTED}",
    ),
    # Basic and Bearer credentials anywhere. The value must look like one (a digit or a
    # base64/token symbol in it, a long Bearer value, a Basic value that decodes to a pair)
    # so prose such as "basic authentication" is left alone, and the realm of a challenge is
    # no credential.
    (
        re.compile(rf"(?i)\b(basic|bearer)\s+(?!realm\b)({_TOKEN}{{8,}}=*)"),
        _scheme_credential,
    ),
    # The userinfo of any URL (a feed's user:pass@, a database or cache URL): everything up
    # to the last @ of the authority, as urlsplit and httpx read it (a password may hold an
    # @ or a '). The scheme is bounded: unbounded, every word boundary of a long run such
    # as "a.a.a.…" rescanned the rest of it (quadratic, seconds for one request path).
    (
        re.compile(rf"(?i)\b([a-z][a-z0-9+.-]{{0,{MAX_SCHEME - 1}}}://)[^\s/?#\"<>]+@"),
        rf"\g<1>{REDACTED}@",
    ),
    # A feed's media key (/feeds/{id}/k/{key}/, ADR 0020).
    (re.compile(rf"(/feeds/{_SEGMENT}/k/){_SEGMENT}"), rf"\g<1>{REDACTED}"),
    # The one-time token of an upload URL (/uploads/{id}/{token}), whatever punctuation
    # follows it. Object store keys under uploads/{id}/ end in a file name with an extension
    # and are left alone.
    (
        re.compile(rf"(/uploads/{_SEGMENT}/)[A-Za-z0-9_-]{{16,}}(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"),
        rf"\g<1>{REDACTED}",
    ),
    # An MCP API key (cck_ plus 54 characters); its displayable head (cck_ plus 8) is no
    # secret and stays readable.
    (re.compile(r"\b(cck_[A-Za-z0-9_-]{8})[A-Za-z0-9_-]+"), rf"\g<1>{REDACTED}"),
)
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "proxy-authorization",
        "cookie",
        "set_cookie",
        "set-cookie",
        "password",
    }
)
"""Event keys, and keys of nested mappings such as a headers dict, whose value is a secret
whatever it looks like."""


def redact_text(text: str) -> str:
    """``text`` with every credential Copycast knows the shape of replaced by ``***``."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _is_sensitive(key: object) -> bool:
    return isinstance(key, str) and key.lower() in SENSITIVE_KEYS


def redact_value(value: Any) -> Any:
    """Strings redacted, mappings and sequences walked into new containers; anything else
    (numbers, None, objects a renderer will ``repr``) as is."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        mapping: Mapping[object, object] = value  # pyright: ignore[reportUnknownVariableType]
        return {
            key: REDACTED if _is_sensitive(key) else redact_value(item)
            for key, item in mapping.items()
        }
    if isinstance(value, list | tuple):
        sequence: list[object] | tuple[object, ...] = value  # pyright: ignore[reportUnknownVariableType]
        redacted = [redact_value(item) for item in sequence]
        return redacted if isinstance(sequence, list) else tuple(redacted)
    return value


def redact_secrets(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """structlog processor: every value of the event through :func:`redact_value`, and the
    value of a :data:`SENSITIVE_KEYS` key replaced whole."""
    for key, value in event_dict.items():
        event_dict[key] = REDACTED if _is_sensitive(key) else redact_value(value)
    return event_dict


class RedactingRenderer:
    """A renderer whose output goes through :func:`redact_text` once more (the console
    renderer only: see the module docstring)."""

    def __init__(self, renderer: Processor) -> None:
        self._renderer = renderer

    def __call__(self, logger: Any, name: str, event_dict: EventDict) -> Any:
        rendered = self._renderer(logger, name, event_dict)
        return redact_text(rendered) if isinstance(rendered, str) else rendered


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
        renderer = RedactingRenderer(
            structlog.dev.ConsoleRenderer(
                colors=sys.stderr.isatty(), exception_formatter=structlog.dev.plain_traceback
            )
        )
    # Last of the shared chain: after format_exc_info, so a JSON traceback is redacted too.
    shared.append(redact_secrets)

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
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
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

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(process=process)


def bind_context(**values: Any) -> None:
    """Bind request/job identifiers for the current context (None values are dropped)."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in values.items() if v is not None})


def unbind_context(*keys: str) -> None:
    structlog.contextvars.unbind_contextvars(*keys)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name) if name else structlog.stdlib.get_logger()
