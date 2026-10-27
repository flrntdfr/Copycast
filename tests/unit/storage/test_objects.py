"""The Object store API over obstore (``memory://``), Scratch, and ``copycast storage push``."""

from __future__ import annotations

from pathlib import Path

import pytest

from copycast.adapters.storage import objects as objects_module
from copycast.adapters.storage.layout import LAYOUT_VERSION_FILE, Layout, LayoutMismatch
from copycast.adapters.storage.objects import (
    ObjectStore,
    RangeNotSatisfiable,
    StorageUnavailable,
    open_store,
)
from copycast.adapters.storage.push import files_to_push, push
from copycast.adapters.storage.scratch import Scratch
from copycast.settings import SettingsError, StorageSettings

FEED = "0123456789abcdef"
ITEM = "fedcba9876543210"


async def _read(store: ObjectStore, key: str, **kwargs: object) -> bytes:
    return b"".join([chunk async for chunk in store.stream(key, **kwargs)])  # type: ignore[arg-type]


def test_whole_objects_heads_listings_and_deletes(store: ObjectStore, tmp_path: Path) -> None:
    assert store.get_bytes("a/missing") is None and store.head("a/missing") is None
    assert store.put_bytes("a/one.txt", "one", content_type="text/plain") == 3
    store.put_bytes("a/b/two.bin", b"\0\1")
    store.put_bytes("c/three", b"3")
    head = store.head("a/one.txt")
    assert head is not None and head.size == 3 and head.name == "one.txt"
    assert head.last_modified.tzinfo is not None and head.etag
    assert store.get_text("a/one.txt") == "one" and store.exists("a/b/two.bin")
    assert [o.key for o in store.list("a/")] == ["a/b/two.bin", "a/one.txt"]
    assert store.list_dirs("") == ["a", "c"] and store.list_dirs("a/") == ["b"]

    local = tmp_path / "upload.bin"
    local.write_bytes(b"x" * 1000)
    assert store.put_file("a/up.bin", local) == 1000
    assert store.download("a/up.bin", tmp_path / "down" / "copy.bin")
    assert (tmp_path / "down" / "copy.bin").read_bytes() == b"x" * 1000
    assert not store.download("a/none", tmp_path / "none")

    store.delete(["a/one.txt", "a/never-there"])
    assert not store.exists("a/one.txt")
    assert store.delete_prefix("a/") == 2 and store.list("a/") == []
    assert store.exists("c/three")


async def test_ranged_streams_are_pinned_to_the_version_head_saw(store: ObjectStore) -> None:
    store.put_bytes("m/x", b"0123456789")
    meta = store.head("m/x")
    assert meta is not None
    assert await _read(store, "m/x") == b"0123456789"
    assert await _read(store, "m/x", start=2, end=5) == b"234"
    assert await _read(store, "m/x", start=7) == b"789"
    assert await _read(store, "m/x", if_match=meta.etag) == b"0123456789"
    store.put_bytes("m/x", b"replaced!!")  # a new version: the old ETag no longer matches
    with pytest.raises(FileNotFoundError):
        await _read(store, "m/x", if_match=meta.etag)
    with pytest.raises(FileNotFoundError):
        await _read(store, "m/absent")
    with pytest.raises(RangeNotSatisfiable):
        await _read(store, "m/x", start=20, end=25)
    assert (await store.head_async("m/x")) is not None
    assert await store.get_bytes_async("m/x") == b"replaced!!"
    await store.put_bytes_async("m/y", b"y")
    assert await store.get_bytes_async("m/y") == b"y"
    assert await store.get_bytes_async("m/none") is None
    assert await store.head_async("m/none") is None


def test_open_store_refuses_other_schemes_and_shares_memory_stores() -> None:
    one = open_store(StorageSettings(url="memory://shared-x"))
    two = open_store(StorageSettings(url="memory://shared-x"))
    one.put_bytes("k", b"v")
    assert two.get_bytes("k") == b"v"
    objects_module.forget_memory_store("shared-x")
    assert open_store(StorageSettings(url="memory://shared-x")).get_bytes("k") is None
    objects_module.forget_memory_store("shared-x")
    s3 = open_store(StorageSettings(url="s3://bucket/prefix", endpoint="http://127.0.0.1:9"))
    assert s3.url == "s3://bucket/prefix"
    with pytest.raises(StorageUnavailable, match="unreachable or refused"):
        s3.ping()
    with pytest.raises(SettingsError):
        open_store(StorageSettings.model_construct(url="ftp://nope"))


def test_scratch_jobs_are_private_and_swept(tmp_path: Path) -> None:
    scratch = Scratch(tmp_path / "scratch")
    with scratch.job(ITEM) as one, scratch.job(ITEM) as two:
        assert one.root != two.root and one.home.is_dir() and two.temp.is_dir()
        (one.temp / "x.part").write_bytes(b"x")
    assert not one.root.exists() and not two.root.exists()
    with pytest.raises(RuntimeError), scratch.job("boom") as job:
        raise RuntimeError("the fetch failed")
    assert not job.root.exists()
    leftover = scratch.root / "jobs" / "crashed.0000"
    (leftover / "home").mkdir(parents=True)
    scratch.temp_file(".txt").write_text("x")
    assert scratch.sweep() == 2 and not leftover.exists()
    assert scratch.free_bytes() is not None


def _data_dir(root: Path) -> Path:
    (root / "feeds" / FEED / "media").mkdir(parents=True)
    (root / "feeds" / FEED / "tmp").mkdir()
    (root / "feeds" / FEED / "assets").mkdir()
    (root / "engine").mkdir()
    (root / LAYOUT_VERSION_FILE).write_text("1\n")
    (root / "engine" / "defaults.json").write_text('{"language": "fr"}')
    (root / "feeds" / FEED / "feed.json").write_text("{}")
    (root / "feeds" / FEED / "media" / f"{ITEM}.m4a").write_bytes(b"\0" * 100)
    (root / "feeds" / FEED / "media" / f"{ITEM}.info.json").write_text("{}")
    (root / "feeds" / FEED / "tmp" / f"{ITEM}.m4a.part").write_bytes(b"\0")
    (root / "feeds" / FEED / "assets" / f"{ITEM}.artwork.jpg").write_bytes(b"jpg")
    (root / "feeds" / FEED / "assets" / ".feed.json.ab12.tmp").write_text("x")
    return root


def test_push_copies_a_data_directory_once_and_resumes(tmp_path: Path) -> None:
    source = _data_dir(tmp_path / "data")
    keys = [key for key, _ in files_to_push(source)]
    assert keys == [
        "engine/defaults.json",
        f"feeds/{FEED}/assets/{ITEM}.artwork.jpg",
        f"feeds/{FEED}/media/{ITEM}.info.json",
        f"feeds/{FEED}/media/{ITEM}.m4a",
        f"feeds/{FEED}/feed.json",
    ], "partials, temp files and tmp/ stay behind; descriptors go last"

    store = open_store(StorageSettings(url="memory://push-test"))
    try:
        layout = Layout(store)
        dry = push(layout, source, dry_run=True)
        assert (dry.files, dry.uploaded, dry.bytes_uploaded) == (5, 5, 125)
        assert store.list("") == [] and not dry.layout_version_written
        report = push(layout, source)
        assert report.uploaded == 5 and report.failures == []
        assert report.layout_version_written and layout.read_version() == "2"
        assert store.get_bytes(f"feeds/{FEED}/media/{ITEM}.m4a") == b"\0" * 100
        assert layout.ensure_version() == "2"
        again = push(layout, source)
        assert (again.uploaded, again.skipped_present) == (0, 5)
        assert not again.layout_version_written
        assert (source / "feeds" / FEED / "media" / f"{ITEM}.m4a").is_file(), "left untouched"
    finally:
        objects_module.forget_memory_store("push-test")


def test_push_refuses_what_is_not_a_layout_1_directory(tmp_path: Path, layout: Layout) -> None:
    with pytest.raises(SettingsError, match="not a Copycast data directory"):
        push(layout, tmp_path)
    (tmp_path / LAYOUT_VERSION_FILE).write_text("2\n")
    with pytest.raises(LayoutMismatch, match="copies layout '1'"):
        push(layout, tmp_path)
    source = _data_dir(tmp_path / "data")
    layout.store.put_bytes(LAYOUT_VERSION_FILE, "7\n")
    with pytest.raises(LayoutMismatch, match="'7'"):
        push(layout, source)


def test_push_reports_failures_and_leaves_the_bucket_unversioned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _data_dir(tmp_path / "data")
    store = open_store(StorageSettings(url="memory://push-fail"))
    try:
        real = store.put_file

        def flaky(key: str, path: Path, *, content_type: str | None = None) -> int:
            if key.endswith(".m4a"):
                raise OSError("connection reset")
            return real(key, path, content_type=content_type)

        monkeypatch.setattr(store, "put_file", flaky)
        report = push(Layout(store), source)
        assert [f.key for f in report.failures] == [f"feeds/{FEED}/media/{ITEM}.m4a"]
        assert not report.layout_version_written and store.get_bytes(LAYOUT_VERSION_FILE) is None
        with pytest.raises(LayoutMismatch, match="contains feeds but no LAYOUT_VERSION"):
            Layout(store).ensure_version()
    finally:
        objects_module.forget_memory_store("push-fail")
