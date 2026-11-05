"""Secrets never reach a log line: events go through the processors ``configure_logging`` sets."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any, Literal

import pytest
import structlog

from copycast.logging import (
    REDACTED,
    configure_logging,
    get_logger,
    redact_secrets,
    redact_text,
    redact_value,
)

FEED_PAIR_URL = "https://u7Gh2kLm:Pq9Rs8Tu7Vw6Xy5Z@copycast.example/feeds/abc123.xml"
MEDIA_KEY_PATH = "/feeds/abc123/k/Zr8q2LmNoPq4StUv/media/0123456789abcdef.m4a"
UPLOAD_PATH = "/uploads/0123456789abcdef/AbCdEfGhIjKlMnOpQrStUvWxYz012345"
API_KEY = "cck_ABCDEFGH" + "9" * 46
BASIC = "Basic Y29weWNhc3Q6aHVudGVyMg=="  # copycast:hunter2
BEARER = f"Bearer {API_KEY}"
SECRETS = (
    "u7Gh2kLm",
    "Pq9Rs8Tu7Vw6Xy5Z",
    "Zr8q2LmNoPq4StUv",
    "AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "9" * 46,
    "Y29weWNhc3Q6aHVudGVyMg",
    "hunter2",
)


def assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked into {text!r}"


def configured(fmt: Literal["console", "json"]) -> io.StringIO:
    """The real configuration with its stderr handler pointed at a buffer."""
    configure_logging(process="test", fmt=fmt, level="DEBUG")
    buffer = io.StringIO()
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(buffer)  # pyright: ignore[reportUnknownMemberType]
    return buffer


@pytest.fixture
def restore_logging() -> Iterator[None]:
    try:
        yield
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        structlog.reset_defaults()
        structlog.contextvars.clear_contextvars()


@pytest.fixture
def json_logs(restore_logging: None) -> Callable[[], list[dict[str, Any]]]:
    buffer = configured("json")
    return lambda: [json.loads(line) for line in buffer.getvalue().splitlines() if line]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FEED_PAIR_URL, "https://***@copycast.example/feeds/abc123.xml"),
        ("http://token@host/x", "http://***@host/x"),
        ("postgresql+psycopg://copycast:pw@db:5432/x", "postgresql+psycopg://***@db:5432/x"),
        (MEDIA_KEY_PATH, "/feeds/abc123/k/***/media/0123456789abcdef.m4a"),
        (UPLOAD_PATH, "/uploads/0123456789abcdef/***"),
        (f"key {API_KEY} minted", "key cck_ABCDEFGH*** minted"),
        (f"Authorization: {BASIC}", "Authorization: ***"),
        ('{"authorization": "opaque-value"}', '{"authorization": "***"}'),
        (f"sent {BASIC} upstream", "sent Basic *** upstream"),
        (f"sent {BEARER}", "sent Bearer ***"),
    ],
)
def test_redact_text_masks_every_known_secret(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "https://copycast.example/feeds/abc123.xml",
        "mail a@b.example, see http://host/a?e=x@y",
        "/feeds/abc123/media/0123456789abcdef.m4a",
        "/api/uploads/0123456789abcdef",
        "uploads/0123456789abcdef/file.mp3 in s3://bucket/uploads/0123456789abcdef/file.mp3",
        "cck_ABCDEFGH",  # the displayable head of a key is no secret
        'WWW-Authenticate: Basic realm="Copycast", charset="UTF-8"',
        "basic authentication is configured; bearer tokens are minted on the MCP page",
    ],
)
def test_redact_text_leaves_what_is_no_secret(text: str) -> None:
    assert redact_text(text) == text


def test_redact_value_walks_containers_and_masks_sensitive_keys() -> None:
    value = {
        "headers": {"Authorization": "anything at all", "Cookie": "sid=1", "accept": "*/*"},
        "urls": [FEED_PAIR_URL, (MEDIA_KEY_PATH, 3)],
        "count": 3,
    }
    redacted = redact_value(value)
    assert redacted == {
        "headers": {"Authorization": REDACTED, "Cookie": REDACTED, "accept": "*/*"},
        "urls": [
            "https://***@copycast.example/feeds/abc123.xml",
            ("/feeds/abc123/k/***/media/0123456789abcdef.m4a", 3),
        ],
        "count": 3,
    }
    assert value["urls"][0] == FEED_PAIR_URL  # the caller's object is left alone


def test_redact_secrets_processor() -> None:
    event = redact_secrets(
        None,
        "info",
        {"event": f"fetched {FEED_PAIR_URL}", "authorization": BASIC, "password": "pw", "n": 1},
    )
    assert event == {
        "event": "fetched https://***@copycast.example/feeds/abc123.xml",
        "authorization": REDACTED,
        "password": REDACTED,
        "n": 1,
    }


def test_structlog_events_are_redacted_by_the_configured_chain(json_logs: Any) -> None:
    get_logger("copycast.test").info(
        "feed.created",
        feed_url=FEED_PAIR_URL,
        path=MEDIA_KEY_PATH,
        upload_url=f"https://copycast.example{UPLOAD_PATH}",
        headers={"authorization": BEARER},
        note=f"client sent {BASIC}",
        api_key=API_KEY,
    )
    (line,) = json_logs()
    assert line["event"] == "feed.created"
    assert line["feed_url"] == "https://***@copycast.example/feeds/abc123.xml"
    assert line["path"] == "/feeds/abc123/k/***/media/0123456789abcdef.m4a"
    assert line["upload_url"] == "https://copycast.example/uploads/0123456789abcdef/***"
    assert line["headers"] == {"authorization": REDACTED}
    assert line["note"] == "client sent Basic ***"
    assert line["api_key"] == "cck_ABCDEFGH***"
    assert_clean(json.dumps(line))


def test_stdlib_records_and_tracebacks_are_redacted(json_logs: Any) -> None:
    stdlib = logging.getLogger("httpx")
    stdlib.warning("HTTP Request: GET %s", FEED_PAIR_URL)
    try:
        raise RuntimeError(f"upstream refused {FEED_PAIR_URL} with {BASIC}")
    except RuntimeError:
        get_logger("copycast.test").exception("refresh.failed")
    first, second = json_logs()
    assert first["event"] == "HTTP Request: GET https://***@copycast.example/feeds/abc123.xml"
    assert "https://***@copycast.example" in second["exception"]
    assert_clean(json.dumps([first, second]))


def test_console_tracebacks_are_redacted(restore_logging: None) -> None:
    buffer = configured("console")
    try:
        raise RuntimeError(f"upstream refused {FEED_PAIR_URL} at {MEDIA_KEY_PATH}")
    except RuntimeError:
        get_logger("copycast.test").exception("refresh.failed", feed_url=FEED_PAIR_URL)
    err = buffer.getvalue()
    assert "refresh.failed" in err and "RuntimeError" in err
    assert "/k/***/" in err
    assert_clean(err)
