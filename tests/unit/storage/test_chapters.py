"""upgrade_chapter_files: chapters archived in the Engine's shape become Podcasting 2.0 JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import structlog

from copycast.adapters.storage.chapters import ChaptersUpgrade, upgrade_chapter_files
from copycast.adapters.storage.layout import Layout

FEED = "mirror-chapters"
OTHER = "inbox-abc234"
RAW = "0000000000000001"
P20 = "0000000000000002"
CORRUPT = "0000000000000003"
SOURCE = "0000000000000004"

RAW_CHAPTERS = [
    {"start_time": 63.0, "end_time": 125.25, "title": "Main"},
    {"start_time": 0.0, "end_time": 63.0, "title": "Intro"},
]
P20_TEXT = '{"version": "1.2.0", "chapters": [{"startTime": 0, "title": "Intro"}]}\n'


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    built = Layout(tmp_path / "data")
    built.ensure_version()
    return built


def _write(layout: Layout, feed_id: str, name: str, text: str) -> Path:
    path = layout.assets_dir(feed_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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
    info = layout.media_dir(FEED) / f"{RAW}.info.json"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({"chapters": RAW_CHAPTERS}), encoding="utf-8")
    attachment = _write(
        layout, FEED, f"{RAW}.attachment.0123456789ab.json", json.dumps(RAW_CHAPTERS)
    )
    stray = _write(layout, FEED, "notes.chapters.json", json.dumps(RAW_CHAPTERS))
    untouched = {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (p20, source, info, attachment, stray)
    }

    assert upgrade_chapter_files(layout) == ChaptersUpgrade(scanned=3, upgraded=1, unreadable=0)

    assert json.loads(raw.read_text(encoding="utf-8")) == {
        "version": "1.2.0",
        "chapters": [
            {"startTime": 0, "endTime": 63, "title": "Intro"},
            {"startTime": 63, "endTime": 125.25, "title": "Main"},
        ],
    }
    for path, (content, mtime) in untouched.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == (content, mtime), path.name
    assert not [p for p in layout.assets_dir(FEED).iterdir() if p.name.startswith(".")]


def test_a_second_run_changes_nothing(layout: Layout) -> None:
    raw = _write(layout, FEED, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS))
    assert upgrade_chapter_files(layout).upgraded == 1
    after = (raw.read_bytes(), raw.stat().st_mtime_ns)
    assert upgrade_chapter_files(layout) == ChaptersUpgrade(scanned=1, upgraded=0, unreadable=0)
    assert (raw.read_bytes(), raw.stat().st_mtime_ns) == after


def test_unreadable_files_are_logged_and_the_scan_goes_on(layout: Layout) -> None:
    truncated = _write(layout, FEED, f"{CORRUPT}.chapters.json", '[{"start_time": 0, "ti')
    not_utf8 = layout.assets_dir(FEED) / f"{P20}.chapters.json"
    not_utf8.write_bytes(b"[\xff\xfe\x00garbage")
    too_deep = _write(layout, FEED, f"{SOURCE}.chapters.json", "[" * 100_000 + "]" * 100_000)
    # A broken object is not ours to fix and is skipped without being parsed.
    broken_object = _write(layout, OTHER, f"{CORRUPT}.chapters.json", "{not json")
    # Raw lists that cannot be read from are not replaced by an empty document.
    nothing_usable = _write(layout, OTHER, f"{P20}.chapters.json", '[{"startTime": 0}]')
    raw = _write(layout, OTHER, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS))
    before = {
        p: p.read_bytes() for p in (truncated, not_utf8, too_deep, broken_object, nothing_usable)
    }

    with structlog.testing.capture_logs() as events:
        upgrade = upgrade_chapter_files(layout)

    assert upgrade == ChaptersUpgrade(scanned=6, upgraded=1, unreadable=3)
    skipped = [e for e in events if e["event"] == "chapters.upgrade_skipped"]
    assert sorted(e["file"] for e in skipped) == sorted(
        p.name for p in (truncated, not_utf8, too_deep)
    )
    assert all(e["feed_id"] == FEED and e["log_level"] == "warning" for e in skipped)
    for path, content in before.items():
        assert path.read_bytes() == content, path.name
    assert json.loads(raw.read_text(encoding="utf-8"))["version"] == "1.2.0"


def test_a_file_that_cannot_be_rewritten_is_counted_unreadable(
    layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _write(layout, FEED, f"{RAW}.chapters.json", json.dumps(RAW_CHAPTERS))
    original = raw.read_bytes()

    def boom(*_: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "replace", boom)
    with structlog.testing.capture_logs() as events:
        upgrade = upgrade_chapter_files(layout)
    assert upgrade == ChaptersUpgrade(scanned=1, upgraded=0, unreadable=1)
    assert [e["error"] for e in events] == ["read-only file system"]
    assert raw.read_bytes() == original


def test_an_empty_data_directory_and_feeds_without_assets(layout: Layout) -> None:
    assert upgrade_chapter_files(layout) == ChaptersUpgrade()
    layout.media_dir(FEED).mkdir(parents=True)
    assert upgrade_chapter_files(layout) == ChaptersUpgrade()
    assert upgrade_chapter_files(Layout(layout.root / "missing")) == ChaptersUpgrade()
