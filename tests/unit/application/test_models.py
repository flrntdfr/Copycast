from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from copycast.application.models import (
    BackfillRequest,
    FeedRead,
    InboxCreate,
    InboxRead,
    InboxUpdate,
    MirrorCreate,
    MirrorRead,
    MirrorUpdate,
    Problem,
    PruneRequest,
    SelectionRequest,
)
from copycast.domain.engine_options import EngineOptionRejected
from copycast.domain.enums import BackfillMode, FeedKind, Numbering

NOW = datetime(2024, 3, 1, tzinfo=UTC)

MIRROR: dict[str, Any] = {
    "id": "0123456789abcdef",
    "kind": "mirror",
    "title": "T",
    "feed_url": "http://localhost:8080/feeds/0123456789abcdef.xml",
    "created_at": NOW,
    "source_url": "https://podcast.example/feed.xml",
    "source_kind": "rss",
    "backfill": {"mode": "latest", "latest_n": 5},
    "health": {"status": "never"},
}
INBOX: dict[str, Any] = {
    "id": "copycast-abc234",
    "kind": "inbox",
    "title": "Copycast",
    "name": "Copycast",
    "feed_url": "http://localhost:8080/feeds/copycast-abc234.xml",
    "created_at": NOW,
}


def test_feed_read_discriminates_on_kind() -> None:
    adapter = TypeAdapter(FeedRead)
    mirror = adapter.validate_python(MIRROR)
    inbox = adapter.validate_python(INBOX)
    assert isinstance(mirror, MirrorRead) and mirror.kind is FeedKind.mirror
    assert isinstance(inbox, InboxRead) and inbox.kind is FeedKind.inbox
    assert mirror.counts.listed == 0 and mirror.selection is None
    assert adapter.validate_json(adapter.dump_json(mirror)) == mirror
    with pytest.raises(ValidationError):
        adapter.validate_python({**INBOX, "kind": "mirror"})


def test_read_models_are_frozen() -> None:
    inbox = InboxRead.model_validate(INBOX)
    with pytest.raises(ValidationError):
        inbox.name = "x"  # type: ignore[misc]


def test_mirror_create_defaults_follow_on_and_backfill_all() -> None:
    m = MirrorCreate(source_url=" https://podcast.example/feed.xml ")
    assert m.source_url == "https://podcast.example/feed.xml"
    assert m.backfill.mode is BackfillMode.all
    assert m.follow is True and m.effective_follow is True
    assert m.engine_options == {}


def test_selection_implies_selection_mode_and_follow_off() -> None:
    m = MirrorCreate.model_validate(
        {"source_url": "https://p.example/f.xml", "backfill": {"selection": "1-42,180"}}
    )
    assert m.backfill.mode is BackfillMode.selection
    assert m.backfill.selection == "1-42,180"
    assert m.follow is False


def test_follow_can_still_be_forced_on_under_selection() -> None:
    m = MirrorCreate.model_validate(
        {"source_url": "https://p.example/f.xml", "backfill": {"selection": "1"}, "follow": True}
    )
    assert m.follow is True


def test_contradicting_mode_and_selection_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"backfill\.mode must be 'selection'"):
        BackfillRequest(mode=BackfillMode.all, selection="1-3")


def test_latest_requires_n_and_n_is_dropped_otherwise() -> None:
    with pytest.raises(ValidationError, match="requires latest_n"):
        BackfillRequest(mode=BackfillMode.latest)
    with pytest.raises(ValidationError):
        BackfillRequest(mode=BackfillMode.latest, latest_n=0)
    assert BackfillRequest(mode=BackfillMode.latest, latest_n=5).latest_n == 5
    assert BackfillRequest(mode=BackfillMode.all, latest_n=5).latest_n is None
    with pytest.raises(ValidationError):
        BackfillRequest(mode=BackfillMode.rolling)
    assert BackfillRequest(mode=BackfillMode.rolling, latest_n=10).retention_days is None
    automatic = BackfillRequest(mode=BackfillMode.automatic)
    assert automatic.retention_days == 7 and automatic.latest_n is None
    assert BackfillRequest(mode=BackfillMode.automatic, retention_days=None).retention_days is None
    assert BackfillRequest(mode=BackfillMode.automatic, retention_days=30).retention_days == 30
    with pytest.raises(ValidationError):
        BackfillRequest(mode=BackfillMode.automatic, retention_days=0)
    assert BackfillRequest(mode=BackfillMode.all, retention_days=30).retention_days is None


def test_mirror_create_rejects_owned_and_global_only_engine_options() -> None:
    with pytest.raises(EngineOptionRejected) as excinfo:
        MirrorCreate(
            source_url="https://p.example/f.xml", engine_options={"proxy": "x", "quiet": 1}
        )
    assert excinfo.value.keys == ["proxy", "quiet"]
    ok = MirrorCreate(
        source_url="https://p.example/f.xml", engine_options={"subtitleslangs": ["de"]}
    )
    assert ok.engine_options == {"subtitleslangs": ["de"]}


def test_mirror_create_rejects_blank_url_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MirrorCreate(source_url="   ")
    with pytest.raises(ValidationError):
        MirrorCreate.model_validate({"source_url": "https://p.example", "title": "nope"})


def test_mirror_update_tracks_fields_set() -> None:
    patch = MirrorUpdate.model_validate({"follow": False})
    assert patch.model_fields_set == {"follow"}
    with pytest.raises(EngineOptionRejected):
        MirrorUpdate(engine_options={"cookiefile": "/x"})


def test_selection_request_needs_something() -> None:
    with pytest.raises(ValidationError, match="selection expression or item_ids"):
        SelectionRequest()
    with pytest.raises(ValidationError):
        SelectionRequest(selection="  ")
    req = SelectionRequest(selection="1-3")
    assert req.numbering is Numbering.source and req.dry_run is False
    assert SelectionRequest(item_ids=["a"]).item_ids == ["a"]


def test_inbox_names_are_whitespace_normalized() -> None:
    assert InboxCreate(name="  Read   Later ").name == "Read Later"
    with pytest.raises(ValidationError):
        InboxCreate(name="   ")
    with pytest.raises(ValidationError):
        InboxCreate(name="x", autoprune_days=0)
    patch = InboxUpdate.model_validate({"autoprune_days": None})
    assert patch.model_fields_set == {"autoprune_days"} and patch.autoprune_days is None


def test_prune_request_requires_a_criterion() -> None:
    with pytest.raises(ValidationError, match="at least one criterion"):
        PruneRequest()
    assert PruneRequest(downloaded=True).older_than_days is None
    assert PruneRequest(older_than_days=0).downloaded is False
    assert PruneRequest(downloaded=True, older_than_days=30, dry_run=True).dry_run is True


def test_problem_carries_extension_members() -> None:
    problem = Problem(
        type=Problem.problem_type("feed-exists"),
        title="Feed exists",
        status=409,
        existing_feed_id="abc",
    )
    assert problem.type == "urn:copycast:problem:feed-exists"
    assert problem.model_dump()["existing_feed_id"] == "abc"
