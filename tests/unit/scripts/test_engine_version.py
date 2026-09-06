from __future__ import annotations

from pathlib import Path

import pytest
from scripts import engine_version

from tests.support.origin import FIXTURES_DIR

FIXTURE_LOCK = FIXTURES_DIR / "lock" / "uv.lock"


def test_reads_the_pinned_engine_from_the_fixture_lock() -> None:
    assert engine_version.locked_version(FIXTURE_LOCK) == "2026.8.19"


def test_reads_the_repository_lock_by_default() -> None:
    version = engine_version.locked_version()
    assert version
    assert version[0].isdigit()


def test_other_packages_can_be_looked_up(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'version = 1\n[[package]]\nname = "httpx"\nversion = "0.28.1"\n'
        '[[package]]\nname = "yt-dlp"\nversion = "2026.9.1.123456.dev0"\n',
        encoding="utf-8",
    )
    assert engine_version.locked_version(lock, "httpx") == "0.28.1"
    assert engine_version.locked_version(lock) == "2026.9.1.123456.dev0"


def test_missing_package_raises(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text('version = 1\n[[package]]\nname = "httpx"\nversion = "0.28.1"\n')
    with pytest.raises(engine_version.LockError, match="yt-dlp is not pinned"):
        engine_version.locked_version(lock)


def test_missing_file_and_bad_toml_raise(tmp_path: Path) -> None:
    with pytest.raises(engine_version.LockError, match="not found"):
        engine_version.locked_version(tmp_path / "nope.lock")
    bad = tmp_path / "uv.lock"
    bad.write_text("[[package]\nname = ", encoding="utf-8")
    with pytest.raises(engine_version.LockError, match="not valid TOML"):
        engine_version.locked_version(bad)


def test_main_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert engine_version.main([str(FIXTURE_LOCK)]) == 0
    assert capsys.readouterr().out.strip() == "2026.8.19"


def test_main_reports_errors_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert engine_version.main([str(tmp_path / "missing.lock")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "lockfile not found" in captured.err


def test_main_rejects_extra_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert engine_version.main(["a", "b"]) == 2
    assert "uv.lock" in capsys.readouterr().err
