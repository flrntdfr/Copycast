"""write_atomic: sibling temp file, replace, no leftovers."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from copycast.adapters.storage.atomic import (
    TMP_SUFFIX,
    is_temp_file,
    write_atomic,
    write_json_atomic,
)


def test_write_atomic_creates_parents_and_replaces(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "feed.json"
    assert write_atomic(target, "one") == target
    assert target.read_text() == "one"
    write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["feed.json"]  # no temp files left
    assert oct(target.stat().st_mode & 0o777) == oct(0o644 & ~_umask())


def test_write_atomic_cleans_up_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "feed.json"
    target.write_text("intact")

    def boom(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_atomic(target, "new")
    assert target.read_text() == "intact"
    assert [p.name for p in tmp_path.iterdir()] == ["feed.json"]


def test_write_json_atomic(tmp_path: Path) -> None:
    target = tmp_path / "listing.json"
    write_json_atomic(target, {"b": 1, "a": [1, 2], "when": Path("x")})
    text = target.read_text()
    assert text.endswith("\n")
    assert json.loads(text) == {"b": 1, "a": [1, 2], "when": "x"}
    write_json_atomic(target, {"compact": True}, indent=None)
    assert target.read_text() == '{"compact": true}\n'


def test_is_temp_file() -> None:
    assert is_temp_file(Path(f".feed.json.abcd1234{TMP_SUFFIX}"))
    assert not is_temp_file(Path("feed.json"))
    assert not is_temp_file(Path("feed.json.tmp"))
    assert not is_temp_file(Path(".hidden"))


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current
