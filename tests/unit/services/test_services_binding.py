"""The capability registry after importing the services; gating rules; the probe deadline."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from copycast.application import services
from copycast.application.capabilities import CAPABILITIES, CAPABILITY_NAMES, INTERNAL
from copycast.application.models import (
    MirrorCreate,
    PodcastSearchResult,
    PruneRequest,
    SelectionRequest,
)
from copycast.application.ports import CancelToken
from copycast.application.services.about import normalize_engine_version
from copycast.application.services.items import (
    archive_dedup_key,
    expand_dedup_key,
    prune_dedup_key,
    refresh_dedup_key,
)
from copycast.application.services.mirrors import refresh_allowed
from copycast.application.services.sources import probe_snapshots, search_podcasts
from copycast.domain.enums import JobTrigger
from copycast.domain.exceptions import Unsupported


def test_every_capability_has_a_service_and_a_bound_method() -> None:
    registered = set(CAPABILITIES)
    assert registered == CAPABILITY_NAMES - {"subscribe_events"}
    bound = services.build_services(SimpleNamespace())  # type: ignore[arg-type]
    for name in CAPABILITY_NAMES - INTERNAL - {"subscribe_events"}:
        assert callable(getattr(bound, name)), name
    assert callable(bound.ensure_default_inbox) and callable(bound.resolve_inbox)
    assert CAPABILITIES["create_mirror"].request_model is MirrorCreate
    assert CAPABILITIES["select_items"].request_model is SelectionRequest
    assert CAPABILITIES["prune_inbox"].request_model is PruneRequest
    assert CAPABILITIES["delete_feed"].destructive
    assert CAPABILITIES["about"].description.startswith("Versions and totals")


def test_dedup_keys() -> None:
    assert refresh_dedup_key("f") == "refresh:f"
    assert archive_dedup_key("i") == "archive:i"
    assert expand_dedup_key("r") == "expand:r"
    assert prune_dedup_key("f") == "prune:f"


def _feed(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "paused": False,
        "follow": True,
        "last_refresh_attempt_at": datetime.now(UTC) - timedelta(minutes=5),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize(
    ("trigger", "overrides", "expected"),
    [
        (JobTrigger.manual, {"paused": True, "follow": False}, True),
        (JobTrigger.scheduled, {}, True),
        (JobTrigger.scheduled, {"paused": True}, False),
        (JobTrigger.feed_fetch, {}, False),
        (JobTrigger.feed_fetch, {"last_refresh_attempt_at": None}, True),
        (
            JobTrigger.feed_fetch,
            {"last_refresh_attempt_at": datetime.now(UTC) - timedelta(minutes=16)},
            True,
        ),
        # Pull to refresh lists new items even when Follow is off.
        (JobTrigger.feed_fetch, {"follow": False, "last_refresh_attempt_at": None}, True),
        (JobTrigger.feed_fetch, {"paused": True, "last_refresh_attempt_at": None}, False),
    ],
)
def test_refresh_allowed(trigger: JobTrigger, overrides: dict[str, Any], expected: bool) -> None:
    assert refresh_allowed(_feed(**overrides), trigger, cooldown_minutes=15) is expected


class _SlowGateway:
    def __init__(self) -> None:
        self.token: CancelToken | None = None
        self.searches: list[tuple[str, int]] = []

    def probe(self, url: str, *, options: Any, cancel: CancelToken) -> list[Any]:
        self.token = cancel
        while not cancel.cancelled:
            time.sleep(0.01)
        return []

    def search_podcasts(self, query: str, limit: int) -> list[PodcastSearchResult]:
        self.searches.append((query, limit))
        return [PodcastSearchResult(title=query, feed_url="https://x.example/feed.xml")]


async def test_probe_deadline_sets_the_token_and_raises_unsupported() -> None:
    gateway = _SlowGateway()
    settings = SimpleNamespace(engine=SimpleNamespace(options={}))
    ctx = SimpleNamespace(sources=gateway, settings=settings)
    with pytest.raises(Unsupported, match="longer than 0 seconds"):
        await probe_snapshots(ctx, "https://x.example", deadline_seconds=0.05)  # type: ignore[arg-type]
    assert gateway.token is not None and gateway.token.cancelled


async def test_search_normalizes_query_and_limit() -> None:
    gateway = _SlowGateway()
    ctx = SimpleNamespace(sources=gateway)
    empty = await search_podcasts(ctx, "   ", 10)  # type: ignore[arg-type]
    assert empty.results == [] and empty.query == ""
    assert gateway.searches == [], "a blank query never reaches iTunes"
    page = await search_podcasts(ctx, "  accidental   tech ", 500)  # type: ignore[arg-type]
    assert page.query == "accidental tech"
    assert page.results[0].title == "accidental tech"
    assert gateway.searches == [("accidental tech", 50)], "the limit is capped at 50"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026.08.19", "2026.8.19"),
        (" 2026.8.19 ", "2026.8.19"),
        ("0.0.0", "0.0.0"),
        ("dev-x", "dev-x"),
    ],
)
def test_engine_version_is_normalized_to_pep440(raw: str, expected: str) -> None:
    assert normalize_engine_version(raw) == expected
