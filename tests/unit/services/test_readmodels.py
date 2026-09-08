"""Rows -> read models: health precedence, URLs, media, assets, selection summaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from copycast.application.models import CatalogCounts, InboxRead, MirrorRead
from copycast.application.services import readmodels
from copycast.domain.enums import ArchiveState, HealthStatus

NOW = datetime(2025, 6, 1, tzinfo=UTC)


class Urls:
    """Authentication off: bare feed URLs and no credentials in the read models."""

    def feed_url(
        self, feed_id: str, *, username: str | None = None, password: str | None = None
    ) -> str:
        return f"http://t/feeds/{feed_id}.xml"

    def feed_credentials(self, username: str, password: str) -> None:
        return None

    def media_url(self, feed_id: str, item_id: str, ext: str) -> str:
        return f"http://t/feeds/{feed_id}/media/{item_id}.{ext}"

    def asset_url(self, feed_id: str, local_path: str) -> str:
        return f"http://t/feeds/{feed_id}/assets/{local_path.rsplit('/', 1)[-1]}"


def mirror_row(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "id": "abc",
        "kind": "mirror",
        "is_default": False,
        "title": "Show",
        "title_override": None,
        "description": None,
        "author": None,
        "artwork_url": None,
        "language": "en",
        "auth_username": "k7mpq2xz",
        "auth_password": "p" * 24,
        "created_at": NOW,
        "storage_bytes": 10,
        "revision": 3,
        "intent_version": 2,
        "source_url": "https://x.example/feed.xml",
        "source_dedup_key": "x.example/feed.xml",
        "source_kind": "rss",
        "service": "RSS",
        "source_channel_xml": None,
        "source_etag": None,
        "source_last_modified": None,
        "backfill_mode": "all",
        "backfill_latest_n": None,
        "min_duration_seconds": None,
        "retention_days": None,
        "refresh_interval_hours": None,
        "sync_deletions": False,
        "preferred_language": None,
        "follow": True,
        "paused": False,
        "policy_applied_at": NOW,
        "engine_options": {"ratelimit": 1},
        "autoprune_days": None,
        "last_autoprune_at": None,
        "last_refresh_attempt_at": NOW,
        "last_refresh_success_at": NOW,
        "last_error": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.mark.parametrize(
    ("overrides", "failed", "expected"),
    [
        ({}, 0, HealthStatus.ok),
        ({"paused": True, "last_error": "boom"}, 3, HealthStatus.paused),
        ({"last_error": "HTTP 503"}, 3, HealthStatus.error),
        ({}, 2, HealthStatus.warn),
        ({"last_refresh_attempt_at": None}, 0, HealthStatus.never),
        ({"last_refresh_attempt_at": None}, 1, HealthStatus.warn),
    ],
)
def test_feed_health_precedence(
    overrides: dict[str, Any], failed: int, expected: HealthStatus
) -> None:
    health = readmodels.feed_health(mirror_row(**overrides), CatalogCounts(failed=failed))
    assert health.status is expected
    if expected is HealthStatus.warn:
        assert health.reason == ("1 Episode failed" if failed == 1 else f"{failed} Episodes failed")


def test_mirror_read_and_selection_summary() -> None:
    read = readmodels.mirror_read(
        mirror_row(), Urls(), counts=CatalogCounts(archived=4, available=1), selected_count=9
    )
    assert isinstance(read, MirrorRead)
    assert read.feed_url == "http://t/feeds/abc.xml" and read.episode_count == 4
    assert read.engine_options == {"ratelimit": 1} and read.selection is None
    selection = readmodels.mirror_read(
        mirror_row(backfill_mode="selection"), Urls(), counts=CatalogCounts(), selected_count=9
    ).selection
    assert selection is not None and selection.count == 9 and selection.applied_at == NOW
    with pytest.raises(ValueError):
        readmodels.mirror_read(mirror_row(source_url=None), Urls(), counts=CatalogCounts())


def test_feed_read_dispatches_on_kind() -> None:
    inbox = mirror_row(
        kind="inbox", source_url=None, source_kind=None, backfill_mode=None, autoprune_days=3
    )
    read = readmodels.feed_read(inbox, Urls(), counts=CatalogCounts(archived=2), request_count=5)
    assert isinstance(read, InboxRead)
    assert read.name == "Show" and read.autoprune_days == 3 and read.request_count == 5
    mirror = readmodels.feed_read(mirror_row(), Urls(), counts=CatalogCounts())
    assert isinstance(mirror, MirrorRead)


def item_row(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "id": "0123456789abcdef",
        "feed_id": "abc",
        "source_key": "urn:1",
        "ordinal": 1,
        "source_number": 7,
        "source_season": None,
        "title": "Ep",
        "description": None,
        "author": None,
        "artwork_url": None,
        "published_at": NOW,
        "published_at_approximate": False,
        "duration_seconds": 60,
        "source_url": "https://x.example/1",
        "archivable": True,
        "listed": True,
        "first_seen_at": NOW,
        "archive_state": "archived",
        "wanted_reason": "backfill",
        "attempt_count": 1,
        "last_error": None,
        "archived_at": NOW,
        "media_path": "media/0123456789abcdef.m4a",
        "media_mime": "audio/mp4",
        "media_bytes": 123,
        "media_ext": "m4a",
        "download_count": 2,
        "first_downloaded_at": NOW,
        "last_downloaded_at": NOW,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def asset_row(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "id": "a1",
        "feed_id": "abc",
        "item_id": "0123456789abcdef",
        "kind": "transcript",
        "provenance": "mirrored",
        "language": "en",
        "format": "vtt",
        "remote_url": "https://x.example/t.vtt",
        "local_path": "assets/0123456789abcdef.transcript.en.mirrored.vtt",
        "mime": "text/vtt",
        "size_bytes": 5,
        "state": "archived",
        "last_error": None,
        "fetched_at": NOW,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_item_read_media_and_assets() -> None:
    rid = uuid.uuid4()
    read = readmodels.item_read(item_row(), Urls(), assets=[asset_row()], request_ids=[rid])
    assert read.media is not None
    assert read.media.url == "http://t/feeds/abc/media/0123456789abcdef.m4a"
    assert read.media.bytes == 123 and read.media.ext == "m4a"
    assert read.state is ArchiveState.archived and read.added_at == NOW
    assert read.item_url == "https://x.example/1" and read.request_ids == [rid]
    assert (
        read.assets[0].url
        == "http://t/feeds/abc/assets/0123456789abcdef.transcript.en.mirrored.vtt"
    )
    wanted = readmodels.item_read(
        item_row(archive_state="wanted", media_path=None, media_ext=None, media_bytes=None), Urls()
    )
    assert wanted.media is None and wanted.state is ArchiveState.wanted
    failed_asset = readmodels.asset_read(
        asset_row(state="failed", local_path=None, last_error="too big"), Urls()
    )
    assert failed_asset.url is None and failed_asset.last_error == "too big"


def test_job_read_from_row() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        kind="refresh",
        feed_id="abc",
        item_id=None,
        request_id=None,
        trigger="manual",
        status="queued",
        progress={"phase": "listing"},
        result=None,
        error=None,
        error_kind=None,
        attempt=0,
        created_at=NOW,
        started_at=None,
        finished_at=None,
    )
    read = readmodels.job_read(row)
    assert read.kind.value == "refresh" and read.progress is not None
    assert read.progress.phase.value == "listing"
