"""``copycast users reassign OLD NEW``: the command line (§1.19).

The database and Object store work is tested in
``tests/integration/storage/test_reassign.py``; here the refusals that need neither, the
confirmation and what the command prints.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from copycast.adapters.db import reassign as reassign_module
from copycast.adapters.db.reassign import WORKER_RUNNING, ReassignRefused, ReassignReport
from copycast.cli import main
from copycast.settings import Settings, SettingsError
from tests.support.users import users_auth


@pytest.fixture(autouse=True)
def _users_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST__AUTH__USERS", json.dumps(users_auth()["users"]))


def _fake_reassign(
    monkeypatch: pytest.MonkeyPatch, outcome: ReassignReport | Exception
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def fake(settings: Settings, old: str, new: str, **_: Any) -> ReassignReport:
        assert settings.auth.mode == "users"
        calls.append((old, new))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(reassign_module, "reassign", fake)
    return calls


def test_reassign_is_a_users_command() -> None:
    result = CliRunner().invoke(main, ["users", "reassign", "--help"])
    assert result.exit_code == 0, result.output
    assert "OLD" in result.output and "NEW" in result.output and "--yes" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["florent", "carol"], "carol is not a configured User"),
        (["alice", "alice"], "are the same User"),
        (["Not A Name", "alice"], "is not a username"),
    ],
)
def test_refusals_that_need_no_database(
    args: list[str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_reassign(monkeypatch, ReassignReport(old="x", new="y"))
    result = CliRunner().invoke(main, ["users", "reassign", *args, "--yes"])
    assert result.exit_code == 1
    assert message in result.output
    assert calls == []


def test_it_asks_before_changing_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_reassign(monkeypatch, ReassignReport(old="copycast", new="alice"))
    declined = CliRunner().invoke(main, ["users", "reassign", "copycast", "alice"], input="n\n")
    assert declined.exit_code == 1 and "Aborted" in declined.output
    assert calls == []
    accepted = CliRunner().invoke(main, ["users", "reassign", "copycast", "alice"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert calls == [("copycast", "alice")]


def test_it_prints_what_moved(monkeypatch: pytest.MonkeyPatch) -> None:
    report = ReassignReport(
        old="copycast",
        new="alice",
        feeds=["a" * 16, "b" * 16, "copycast-abcdef"],
        keys=2,
        jobs=5,
        demoted_default="copycast-abcdef",
        files=["users/copycast/defaults.json"],
        conflicts=["users/copycast/cookie-files/yt.txt"],
    )
    _fake_reassign(monkeypatch, report)
    result = CliRunner().invoke(main, ["users", "reassign", "copycast", "alice", "--yes"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines == [
        "copycast's default Local feed copycast-abcdef is now an ordinary Local feed of alice",
        "users/copycast/cookie-files/yt.txt kept: alice has its own",
        "moved from copycast to alice: 3 Feeds, 2 API keys, 5 jobs, 1 files; 1 conflicts",
    ]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ReassignRefused(WORKER_RUNNING), 1),
        (ReassignRefused("copycast and alice mirror the same Source; …"), 1),
        (SettingsError("the database schema is not current; run `copycast migrate`"), 2),
    ],
)
def test_refusals_from_the_database_exit_non_zero(
    error: Exception, code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_reassign(monkeypatch, error)
    result = CliRunner().invoke(main, ["users", "reassign", "copycast", "alice", "--yes"])
    assert result.exit_code == code
    assert result.output == f"copycast: {error}\n"
