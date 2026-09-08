from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from copycast.cli import main
from copycast.version import APP_VERSION


def test_version_prints_app_and_engine() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == f"copycast {APP_VERSION}"
    assert lines[1].startswith("engine ")
    assert APP_VERSION == "1.2.1"


def test_config_prints_redacted_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST__DATABASE_URL", "postgresql://copycast:hunter2@db/copycast")
    monkeypatch.setenv("COPYCAST__DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["database_url"] == "postgresql+psycopg://copycast:***@db/copycast"
    assert "hunter2" not in result.output
    assert data["refresh"]["interval_hours"] == 24
    assert data["data_dir"] == str(tmp_path.resolve())


def test_invalid_config_exits_2_with_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST__BASE_URL", "not-a-url")
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 2
    assert result.output.count("\n") == 1
    assert result.output.startswith("copycast: invalid configuration")


def test_every_plan_command_is_registered() -> None:
    assert set(main.commands) == {"api", "worker", "migrate", "rebuild", "config", "openapi"}
    result = CliRunner().invoke(main, ["rebuild", "--help"])
    assert result.exit_code == 0 and "--yes" in result.output
