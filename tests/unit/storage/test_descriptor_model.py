"""FeedDescriptor as a document: JSON round trip, reading errors, intent version peek."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from copycast.adapters.storage.descriptor import (
    DESCRIPTOR_FORMAT,
    DescriptorAsset,
    DescriptorError,
    DescriptorFeed,
    DescriptorItem,
    DescriptorPolicy,
    DescriptorRequest,
    FeedDescriptor,
    read_intent_version,
)
from copycast.adapters.storage.layout import LAYOUT_VERSION
from copycast.domain.enums import (
    ArchiveState,
    AssetKind,
    AssetState,
    BackfillMode,
    FeedKind,
    RequestedVia,
    RequestStatus,
    SourceKind,
)
from copycast.version import APP_VERSION

NOW = datetime(2025, 6, 1, tzinfo=UTC)


def _descriptor() -> FeedDescriptor:
    return FeedDescriptor(
        exported_at=NOW,
        intent_version=3,
        feed=DescriptorFeed(
            id="0123456789abcdef",
            kind=FeedKind.mirror,
            title="Test",
            source_url="https://podcast.example/feed.xml",
            source_dedup_key="podcast.example/feed.xml",
            source_kind=SourceKind.rss,
            created_at=NOW,
        ),
        policy=DescriptorPolicy(backfill_mode=BackfillMode.latest, backfill_latest_n=5),
        items=[
            DescriptorItem(
                id="fedcba9876543210",
                source_key="urn:x:1",
                ordinal=1,
                title="One",
                first_seen_at=NOW,
                last_listed_at=NOW,
                archive_state=ArchiveState.archived,
                media_path="media/fedcba9876543210.m4a",
            )
        ],
        assets=[
            DescriptorAsset(
                id="aaaaaaaaaaaaaaaa",
                kind=AssetKind.artwork,
                state=AssetState.archived,
                local_path="assets/feed.artwork.jpg",
                created_at=NOW,
            )
        ],
        requests=[
            DescriptorRequest(
                id=uuid4(),
                url="https://x.example/v",
                requested_via=RequestedVia.ui,
                status=RequestStatus.expanded,
                item_ids=["fedcba9876543210"],
                created_at=NOW,
            )
        ],
    )


def test_defaults_and_round_trip(tmp_path: Path) -> None:
    descriptor = _descriptor()
    assert descriptor.format == DESCRIPTOR_FORMAT
    assert descriptor.layout_version == LAYOUT_VERSION
    assert descriptor.app_version == APP_VERSION
    path = descriptor.write(tmp_path / "feed.json")
    assert path.read_text().endswith("\n")
    assert FeedDescriptor.read(path) == descriptor
    assert read_intent_version(path) == 3
    # Unknown keys from a future version are ignored, not fatal.
    data = json.loads(path.read_text())
    data["future_field"] = {"x": 1}
    data["feed"]["future"] = True
    path.write_text(json.dumps(data))
    assert FeedDescriptor.read(path).intent_version == 3


def test_read_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(DescriptorError, match=r"missing\.json"):
        FeedDescriptor.read(missing)
    assert read_intent_version(missing) is None

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{nope")
    with pytest.raises(DescriptorError, match="not JSON"):
        FeedDescriptor.read(bad_json)
    assert read_intent_version(bad_json) is None

    wrong_format = tmp_path / "wrong.json"
    wrong_format.write_text(json.dumps({"format": "something-else", "intent_version": 2}))
    with pytest.raises(DescriptorError, match=f"not a {DESCRIPTOR_FORMAT}"):
        FeedDescriptor.read(wrong_format)
    assert read_intent_version(wrong_format) == 2

    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2]")
    with pytest.raises(DescriptorError, match="not a"):
        FeedDescriptor.read(not_object)
    assert read_intent_version(not_object) is None

    invalid = tmp_path / "invalid.json"
    data = json.loads(_descriptor().to_json())
    data["feed"]["kind"] = "channel"
    data["items"][0]["ordinal"] = "first"
    data["intent_version"] = "three"
    invalid.write_text(json.dumps(data))
    with pytest.raises(DescriptorError, match=r"3 invalid field\(s\)"):
        FeedDescriptor.read(invalid)
    assert read_intent_version(invalid) is None
    bool_version = tmp_path / "bool.json"
    bool_version.write_text(json.dumps({"format": DESCRIPTOR_FORMAT, "intent_version": True}))
    assert read_intent_version(bool_version) is None  # a bool is not a version
