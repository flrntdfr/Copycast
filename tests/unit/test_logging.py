"""Secrets never reach a log line: events go through the processors ``configure_logging`` sets."""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

import pytest
import structlog

from copycast.logging import (
    MAX_SCHEME,
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
        # Letter-only credentials: base64("user:pass") and a long letter-only token.
        ("rejected Basic dXNlcjpwYXNz", "rejected Basic ***"),
        ("Authorization header Basic dXNlcjpwYXNz", "Authorization header Basic ***"),
        ("(b'authorization', b'Basic dXNlcjpwYXNz')", "(b'authorization', b'Basic ***')"),
        ("sent Bearer abcdefghijklmnopqrstu", "sent Bearer ***"),
        # A Digest value is quoted parameters: all of them go.
        ('Authorization: Digest username="a", response="deadbeef"', "Authorization: ***"),
        # Userinfo up to the last @ of the authority, a quote included.
        ("https://user:pa'ss@host/feed.xml", "https://***@host/feed.xml"),
        ("https://user:p@ss@host/feed.xml?a=b@c", "https://***@host/feed.xml?a=b@c"),
        # An upload token before punctuation.
        (f"see https://h{UPLOAD_PATH}.", "see https://h/uploads/0123456789abcdef/***."),
        (f"(https://h{UPLOAD_PATH})", "(https://h/uploads/0123456789abcdef/***)"),
        (
            f"urls=[{UPLOAD_PATH}, {UPLOAD_PATH}]",
            "urls=[/uploads/0123456789abcdef/***, /uploads/0123456789abcdef/***]",
        ),
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
        "Basic Authentication; Bearer authorization",
        "uploads/0123456789abcdef/AbCdEfGhIjKlMnOpQrStUvWxYz.m4a",
    ],
)
def test_redact_text_leaves_what_is_no_secret(text: str) -> None:
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "a." * 50_000,  # every word boundary starts a scheme candidate
        "/" + "a." * 30_000,  # a request path as uvicorn accepts it
        "a+" * 50_000,
        "a://" * 25_000,
        "x" * 100_000 + "://" + "y" * 100,
    ],
)
def test_redact_text_is_linear_on_long_scheme_like_runs(text: str) -> None:
    """The userinfo rule once rescanned the whole run from every word boundary: 100 KB of
    'a.' took seconds, and an unauthenticated request path froze the api's event loop."""
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 0.05


def test_userinfo_is_redacted_after_a_long_dotted_run() -> None:
    text = "a." * 10_000 + " https://user:hunter2@host.example/x"
    assert redact_text(text).endswith(f" https://{REDACTED}@host.example/x")
    scheme = "s" * MAX_SCHEME
    assert redact_text(f"{scheme}://u:p@h/") == f"{scheme}://{REDACTED}@h/"


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


@pytest.mark.parametrize(
    "detail",
    [
        'upstream said Authorization: "Bearer abc"',
        'proxy-authorization=""',
        'Authorization: tok"rest',
        f'Authorization: "{BASIC}" and {FEED_PAIR_URL}',
        'a "quoted" \\ value with a backslash',
    ],
)
def test_every_json_line_stays_json(json_logs: Any, detail: str) -> None:
    """The JSON line is not re-redacted: a rule matching inside a ``\\"`` escape would cut the
    string short and the line would no longer parse."""
    get_logger("copycast.test").warning("upstream.refused", detail=detail)
    logging.getLogger("httpx").warning("HTTP Request: %s", detail)
    first, second = json_logs()  # json.loads of every line
    assert first["detail"] == redact_text(detail)
    assert second["event"] == redact_text(f"HTTP Request: {detail}")
    assert_clean(json.dumps([first, second]))


def test_console_tracebacks_show_no_locals(restore_logging: None) -> None:
    """A secret in a variable has no shape to match: the console prints no frame locals."""
    buffer = configured("console")

    def fail() -> None:
        media_key = "MEDIAKEYSECRET123"
        password = "hunter2hunter2"
        raise RuntimeError(f"failed with {len(media_key) + len(password)} characters")

    try:
        fail()
    except RuntimeError:
        get_logger("copycast.test").exception("refresh.failed")
    err = buffer.getvalue()
    assert "Traceback (most recent call last)" in err and "RuntimeError" in err
    assert "MEDIAKEYSECRET123" not in err
    assert "hunter2hunter2" not in err


def test_console_tracebacks_keep_a_long_url_on_one_line(restore_logging: None) -> None:
    """A wrapped traceback could split a userinfo away from its @; a plain one never wraps."""
    buffer = configured("console")
    long_url = f"https://u7Gh2kLm:Pq9Rs8Tu7Vw6Xy5Z@{'sub.' * 40}copycast.example/feeds/abc123.xml"
    try:
        raise RuntimeError(f"upstream refused {long_url}")
    except RuntimeError:
        get_logger("copycast.test").exception("refresh.failed")
    assert_clean(buffer.getvalue())
