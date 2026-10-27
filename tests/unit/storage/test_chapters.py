"""upgrade_chapter_files: chapters archived in the Engine's shape become Podcasting 2.0 JSON."""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog

from copycast.adapters.storage.chapters import ChaptersUpgrade, upgrade_chapter_files
from copycast.adapters.storage.layout import Layout

FEED = "mirror-chapters"
OTHER = "bucket-abc234"
RAW = "0000000000000001"
P20 = "0000000000000002"
CORRUPT = "0000000000000003"
SOURCE = "0000000000000004"

RAW_CHAPTERS = [
    {"start_time": 63.0, "end_time": 125.25, "title": "Main"},
    {"start_time": 0.0, "end_time": 63.0, "title": "Intro"},
]
P20_TEXT = '{"version": "1.2.0", "chapters": [{"startTime": 0, "title": "Intro"}]}\n'


def _write(layout: Layout, feed_id: str, name: str, text: str | bytes) -> str:
    key = layout.assets_prefix(feed_id) + name
    layout.store.put_bytes(key, text)
    return key


def _read(layout: Layout, key: str) -> bytes:
    body = layout.store.get_bytes(key)
    assert body is not None, key
    return body


def _version(layout: Layout, key: str) -> tuple[bytes, Any]:
    """The object's bytes and ETag: a rewrite changes the ETag even with the same bytes."""
    head = layout.store.head(key)
    assert head is not None, key
    return _read(layout, key), head.etag


def test_raw_lists_are_rewritten_and_everything_else_left_alone(layout: Layout) -> None:
    raw = _write(layout, FEED, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS, indent=2))
    p20 = _write(layout, FEED, f"{P20}.chapters.json", P20_TEXT)
    # A Source's own chapters are Podcasting 2.0 too, whatever extra keys they carry.
    source = _write(
        layout,
        OTHER,
        f"{SOURCE}.chapters.json",
        '{"version":"1.2.0","author":"Them","chapters":[{"startTime":5,"img":"x.jpg"}]}',
    )
    # Not chapters files, even when they hold the Engine's shape.
    info = layout.info_json_key(FEED, RAW)
    layout.store.put_bytes(info, json.dumps({"chapters": RAW_CHAPTERS}))
    attachment = _write(
        layout, FEED, f"{RAW}.attachment.0123456789ab.json", json.dumps(RAW_CHAPTERS)
    )
    stray = _write(layout, FEED, "notes.chapters.json", json.dumps(RAW_CHAPTERS))
    untouched = {k: _version(layout, k) for k in (p20, source, info, attachment, stray)}

    assert upgrade_chapter_files(layout) == ChaptersUpgrade(scanned=3, upgraded=1, unreadable=0)

    assert json.loads(_read(layout, raw)) == {
        "version": "1.2.0",
        "chapters": [
            {"startTime": 0, "endTime": 63, "title": "Intro"},
            {"startTime": 63, "endTime": 125.25, "title": "Main"},
        ],
    }
    for key, version in untouched.items():
        assert _version(layout, key) == version, key
    assert not [o for o in layout.store.list(layout.assets_prefix(FEED)) if o.name.startswith(".")]


def test_a_second_run_changes_nothing(layout: Layout) -> None:
    raw = _write(layout, FEED, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS))
    assert upgrade_chapter_files(layout).upgraded == 1
    after = _version(layout, raw)
    assert upgrade_chapter_files(layout) == ChaptersUpgrade(scanned=1, upgraded=0, unreadable=0)
    assert _version(layout, raw) == after


def test_unreadable_files_are_logged_and_the_scan_goes_on(layout: Layout) -> None:
    truncated = _write(layout, FEED, f"{CORRUPT}.chapters.json", '[{"start_time": 0, "ti')
    not_utf8 = _write(layout, FEED, f"{P20}.chapters.json", b"[\xff\xfe\x00garbage")
    too_deep = _write(layout, FEED, f"{SOURCE}.chapters.json", "[" * 100_000 + "]" * 100_000)
    # A broken object is not ours to fix and is skipped without being parsed.
    broken_object = _write(layout, OTHER, f"{CORRUPT}.chapters.json", "{not json")
    # Raw lists that cannot be read from are not replaced by an empty document.
    nothing_usable = _write(layout, OTHER, f"{P20}.chapters.json", '[{"startTime": 0}]')
    raw = _write(layout, OTHER, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS))
    before = {
        k: _read(layout, k) for k in (truncated, not_utf8, too_deep, broken_object, nothing_usable)
    }

    with structlog.testing.capture_logs() as events:
        upgrade = upgrade_chapter_files(layout)

    assert upgrade == ChaptersUpgrade(scanned=6, upgraded=1, unreadable=3)
    skipped = [e for e in events if e["event"] == "chapters.upgrade_skipped"]
    assert sorted(e["file"] for e in skipped) == sorted(
        k.rsplit("/", 1)[-1] for k in (truncated, not_utf8, too_deep)
    )
    assert all(e["feed_id"] == FEED and e["log_level"] == "warning" for e in skipped)
    for key, content in before.items():
        assert _read(layout, key) == content, key
    assert json.loads(_read(layout, raw))["version"] == "1.2.0"


def test_a_file_that_cannot_be_rewritten_is_counted_unreadable(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _write(layout, FEED, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS))
    original = _read(layout, raw)

    def boom(*_: object, **__: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(layout.store, "put_bytes", boom)
    with structlog.testing.capture_logs() as events:
        upgrade = upgrade_chapter_files(layout)
    assert upgrade == ChaptersUpgrade(scanned=1, upgraded=0, unreadable=1)
    assert [e["error"] for e in events] == ["read-only file system"]
    assert _read(layout, raw) == original


def test_an_empty_store_and_feeds_without_assets(layout: Layout) -> None:
    assert upgrade_chapter_files(layout) == ChaptersUpgrade()
    layout.store.put_bytes(layout.media_key(FEED, RAW, "m4a"), b"\0")
    assert upgrade_chapter_files(layout) == ChaptersUpgrade()
