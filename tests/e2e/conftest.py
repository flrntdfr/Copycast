"""End-to-end fixtures: a plain httpx client against a running compose stack.

The stack is started outside pytest (``make test-e2e`` or the CI ``e2e`` job)::

    docker compose -f docker-compose.yml -f docker-compose.ci.yml --profile direct up -d --wait
    uv run pytest -m e2e tests/e2e

Environment:
    COPYCAST_E2E_BASE_URL       where the api listens from the test runner
                                (default http://localhost:8080, the ``direct`` profile)
    COPYCAST_E2E_FIXTURES_URL   where the api and worker reach the fixtures server
                                (default http://fixtures:8000, the compose service name)
    COPYCAST_E2E_TIMEOUT        seconds to wait for archiving and readiness (default 180)

Every test here is synchronous: the stack is a black box reached over HTTP, so no
asyncio plumbing is needed and module-scoped fixtures stay trivial.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # makes ``scripts.engine_version`` importable
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL = "http://localhost:8080"
DEFAULT_FIXTURES_URL = "http://fixtures:8000"
DEFAULT_TIMEOUT = 180.0
E2E_FEED_PATH = "/rss/e2e_feed.xml"


@dataclass(frozen=True)
class Stack:
    """Where the compose stack is reachable from the test runner and from itself."""

    base_url: str
    fixtures_url: str
    timeout: float

    @property
    def feed_source_url(self) -> str:
        """The e2e RSS Source as the api and worker containers resolve it."""
        return f"{self.fixtures_url}{E2E_FEED_PATH}"


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 1.0,
    what: str = "condition",
) -> None:
    """Poll ``predicate`` until it is true or ``timeout`` seconds elapsed."""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"{what} did not happen within {timeout:.0f}s")
        time.sleep(interval)


@pytest.fixture(scope="session")
def stack() -> Stack:
    return Stack(
        base_url=os.environ.get("COPYCAST_E2E_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        fixtures_url=os.environ.get("COPYCAST_E2E_FIXTURES_URL", DEFAULT_FIXTURES_URL).rstrip("/"),
        timeout=float(os.environ.get("COPYCAST_E2E_TIMEOUT", DEFAULT_TIMEOUT)),
    )


@pytest.fixture(scope="session")
def http(stack: Stack) -> Iterator[httpx.Client]:
    """A session-wide client; it never follows redirects so 3xx answers stay visible."""
    with httpx.Client(base_url=stack.base_url, timeout=httpx.Timeout(30.0)) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def stack_ready(http: httpx.Client, stack: Stack) -> None:
    """Fail fast with a clear message when nothing listens at the base URL."""

    def live() -> bool:
        try:
            return http.get("/healthz/live").status_code == 200
        except httpx.TransportError:
            return False

    wait_until(live, timeout=min(stack.timeout, 60.0), what=f"api at {stack.base_url}")


def problem(response: httpx.Response) -> dict[str, Any]:
    """The problem+json body of an error response, asserting the media type."""
    assert response.headers["content-type"].startswith("application/problem+json"), (
        response.status_code,
        response.headers.get("content-type"),
        response.text,
    )
    body: dict[str, Any] = response.json()
    return body
