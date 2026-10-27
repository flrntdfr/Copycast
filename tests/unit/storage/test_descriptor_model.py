"""FeedDescriptor as a document: JSON round trip, reading errors, intent version peek."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from copycast.adapters.storage.objects import ObjectStore
from copycast.domain.enums import (
    ArchiveState,
    AssetKind,
    AssetState,
    BackfillMode,
    FeedKind,
    LiveStatus,
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
                public_ext="mp3",
                live_status=LiveStatus.was_live,
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


def _put(store: ObjectStore, key: str, text: str) -> None:
    store.put_bytes(key, text)


def test_defaults_and_round_trip(store: ObjectStore) -> None:
    descriptor = _descriptor()
    assert descriptor.format == DESCRIPTOR_FORMAT
    assert descriptor.layout_version == LAYOUT_VERSION
    assert descriptor.app_version == APP_VERSION
    path = descriptor.write(store, "feed.json")
    assert (store.get_text(path) or "").endswith("\n")
    assert FeedDescriptor.read(store, path) == descriptor
    assert read_intent_version(store, path) == 3
    # Unknown keys from a future version are ignored, not fatal.
    data = json.loads(store.get_text(path) or "")
    data["future_field"] = {"x": 1}
    data["feed"]["future"] = True
    _put(store, path, json.dumps(data))
    assert FeedDescriptor.read(store, path).intent_version == 3
    # A descriptor written before 1.3 names no public_ext: it reads back as unknown.
    assert FeedDescriptor.read(store, path).items[0].public_ext == "mp3"
    assert FeedDescriptor.read(store, path).items[0].live_status is LiveStatus.was_live
    del data["items"][0]["public_ext"]
    del data["items"][0]["live_status"]  # written before 1.3.1
    _put(store, path, json.dumps(data))
    assert FeedDescriptor.read(store, path).items[0].public_ext is None
    assert FeedDescriptor.read(store, path).items[0].live_status is None


def test_read_errors(store: ObjectStore) -> None:
    missing = "missing.json"
    with pytest.raises(DescriptorError, match=r"missing\.json"):
        FeedDescriptor.read(store, missing)
    assert read_intent_version(store, missing) is None

    bad_json = "bad.json"
    _put(store, bad_json, "{nope")
    with pytest.raises(DescriptorError, match="not JSON"):
        FeedDescriptor.read(store, bad_json)
    assert read_intent_version(store, bad_json) is None

    wrong_format = "wrong.json"
    _put(store, wrong_format, json.dumps({"format": "something-else", "intent_version": 2}))
    with pytest.raises(DescriptorError, match=f"not a {DESCRIPTOR_FORMAT}"):
        FeedDescriptor.read(store, wrong_format)
    assert read_intent_version(store, wrong_format) == 2

    not_object = "list.json"
    _put(store, not_object, "[1, 2]")
    with pytest.raises(DescriptorError, match="not a"):
        FeedDescriptor.read(store, not_object)
    assert read_intent_version(store, not_object) is None

    invalid = "invalid.json"
    data = json.loads(_descriptor().to_json())
    data["feed"]["kind"] = "channel"
    data["items"][0]["ordinal"] = "first"
    data["intent_version"] = "three"
    _put(store, invalid, json.dumps(data))
    with pytest.raises(DescriptorError, match=r"3 invalid field\(s\)"):
        FeedDescriptor.read(store, invalid)
    assert read_intent_version(store, invalid) is None
    bool_version = "bool.json"
    _put(store, bool_version, json.dumps({"format": DESCRIPTOR_FORMAT, "intent_version": True}))
    assert read_intent_version(store, bool_version) is None  # a bool is not a version
