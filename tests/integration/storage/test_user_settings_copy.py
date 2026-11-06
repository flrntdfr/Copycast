"""The one-time copy of the legacy Defaults and Cookie files to ``users/{owner}/`` (§2.2)."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from copycast.adapters.storage.layout import Layout
from copycast.adapters.storage.user_settings import (
    MARKER_KEY,
    MoveReport,
    migrate_legacy_settings,
)
from copycast.app import build_container
from copycast.settings import Settings, SettingsError
from tests.integration.conftest import running, settings_with
from tests.support.paths import api
from tests.support.users import users_auth

pytestmark = pytest.mark.integration

YOUTUBE = ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tsecret-value\n"
TWITCH = ".twitch.tv\tTRUE\t/\tTRUE\t1790000000\tauth-token\tt\n"
DEFAULTS = '{"language": "fr", "refresh_interval_hours": 6}\n'


def _seed_legacy(layout: Layout) -> dict[str, str]:
    """A 2.3.0 Object store: Defaults, two Cookie files and the jar merged from them."""
    legacy = {
        layout.defaults_key(): DEFAULTS,
        layout.cookie_file_key("youtube.txt"): YOUTUBE,
        layout.cookie_file_key("twitch.txt"): TWITCH,
        layout.cookies_key(): YOUTUBE + TWITCH,
    }
    for key, text in legacy.items():
        layout.store.put_bytes(key, text)
    return legacy


def _text(layout: Layout, key: str) -> str | None:
    text: str | None = layout.store.get_text(key)
    return text


def test_the_legacy_settings_are_copied_once_and_kept(layout: Layout) -> None:
    legacy = _seed_legacy(layout)

    report = migrate_legacy_settings(layout, "florent")

    assert sorted(report.copied) == sorted(legacy)
    assert report.conflicts == [] and report.already_done is False
    assert _text(layout, "users/florent/defaults.json") == DEFAULTS
    assert _text(layout, "users/florent/cookies.txt") == YOUTUBE + TWITCH
    assert _text(layout, "users/florent/cookie-files/youtube.txt") == YOUTUBE
    assert _text(layout, "users/florent/cookie-files/twitch.txt") == TWITCH
    # 3.0 copies and never deletes: a 2.3.0 image rolled back still finds everything.
    for key, text in legacy.items():
        assert _text(layout, key) == text
    marker: dict[str, Any] = json.loads(_text(layout, MARKER_KEY) or "{}")
    assert marker["owner"] == "florent" and sorted(marker["copied"]) == sorted(legacy)
    assert marker["at"]


def test_the_marker_makes_a_second_run_a_no_op(layout: Layout) -> None:
    _seed_legacy(layout)
    migrate_legacy_settings(layout, "florent")
    # The User edits their Defaults and drops a Cookie file under 3.0; a 2.3.0 rollback
    # meanwhile rewrote the legacy Defaults. Nothing of that is overwritten or re-copied.
    layout.store.put_bytes("users/florent/defaults.json", '{"language": "de"}\n')
    layout.store.delete("users/florent/cookie-files/twitch.txt")
    layout.store.put_bytes(layout.defaults_key(), '{"language": "it"}\n')

    again = migrate_legacy_settings(layout, "florent")

    assert again == MoveReport(copied=[], conflicts=[], already_done=True)
    assert _text(layout, "users/florent/defaults.json") == '{"language": "de"}\n'
    assert not layout.store.exists("users/florent/cookie-files/twitch.txt")
    # Even another owner gets nothing: the copy happened once for the whole instance.
    assert migrate_legacy_settings(layout, "alice").already_done is True
    assert not layout.store.has_any("users/alice/")


def test_a_target_already_present_is_kept_and_reported(layout: Layout) -> None:
    legacy = _seed_legacy(layout)
    layout.store.put_bytes("users/florent/defaults.json", '{"language": "de"}\n')

    report = migrate_legacy_settings(layout, "florent")

    assert report.conflicts == [layout.defaults_key()]
    assert sorted(report.copied) == sorted(k for k in legacy if k != layout.defaults_key())
    assert _text(layout, "users/florent/defaults.json") == '{"language": "de"}\n'
    assert _text(layout, layout.defaults_key()) == DEFAULTS
    marker: dict[str, Any] = json.loads(_text(layout, MARKER_KEY) or "{}")
    assert marker["conflicts"] == [layout.defaults_key()]


def test_a_legacy_jar_without_files_becomes_the_users_jar(layout: Layout) -> None:
    layout.store.put_bytes(layout.cookies_key(), YOUTUBE)
    layout.store.put_bytes(layout.cookie_files_prefix() + "notes.md", "not a cookie file")

    report = migrate_legacy_settings(layout, "copycast")

    assert report.copied == [layout.cookies_key()]
    assert _text(layout, "users/copycast/cookies.txt") == YOUTUBE
    assert not layout.store.has_any("users/copycast/cookie-files/")


def test_nothing_to_copy_still_writes_the_marker(layout: Layout) -> None:
    report = migrate_legacy_settings(layout, "copycast")
    assert report == MoveReport(copied=[], conflicts=[], already_done=False)
    assert layout.store.exists(MARKER_KEY)
    assert [o.key for o in layout.store.list("users/")] == [MARKER_KEY]


def test_racing_processes_copy_the_same_bytes(layout: Layout) -> None:
    legacy = _seed_legacy(layout)
    # Both processes look for the marker before either writes it.
    barrier = threading.Barrier(2)
    real_exists = layout.store.exists

    def exists(key: str) -> bool:
        found = real_exists(key)
        if key == MARKER_KEY:
            barrier.wait(timeout=5)
        return found

    layout.store.exists = exists  # type: ignore[method-assign]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(pool.map(lambda _: migrate_legacy_settings(layout, "florent"), (1, 2)))
    finally:
        del layout.store.exists

    for report in reports:
        assert report.already_done is False
        assert sorted([*report.copied, *report.conflicts]) == sorted(legacy)
    assert _text(layout, "users/florent/defaults.json") == DEFAULTS
    assert _text(layout, "users/florent/cookies.txt") == YOUTUBE + TWITCH
    marker: dict[str, Any] = json.loads(_text(layout, MARKER_KEY) or "{}")
    assert marker["owner"] == "florent"
    assert migrate_legacy_settings(layout, "florent").already_done is True


@pytest.mark.usefixtures("db")
async def test_startup_copies_to_the_owner_of_existing_data(settings: Settings) -> None:
    configured = settings_with(settings, auth=users_auth(owner_of_existing="alice"))
    layout: Layout = build_container(configured).layout
    _seed_legacy(layout)

    async with running(configured) as (container, client):
        assert container.users.owner_of_existing == "alice"
        assert _text(layout, "users/alice/defaults.json") == DEFAULTS
        assert not layout.store.has_any("users/root/")
        # Alice's own settings are now the old global ones; Bob starts from nothing.
        login = await client.post(
            "/auth/login", json={"username": "alice", "password": "alice-password-123"}
        )
        assert login.status_code == 204, login.text
        defaults = (await client.get(api("/settings/defaults"))).json()
        assert defaults["language"] == "fr" and defaults["refresh_interval_hours"] == 6
        cookies = (await client.get(api("/engine/cookies"))).json()
        assert sorted(file["name"] for file in cookies["files"]) == ["twitch", "youtube"]


async def test_open_mode_copies_to_the_implicit_admin(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    # The fixture app's lifespan ran on an empty store; a fresh one finds the legacy keys.
    layout: Layout = build_container(settings).layout
    layout.store.delete(MARKER_KEY)
    _seed_legacy(layout)
    async with running(settings) as (_, fresh):
        defaults = (await fresh.get(api("/settings/defaults"))).json()
    assert defaults["language"] == "fr"
    assert _text(layout, "users/copycast/defaults.json") == DEFAULTS


async def test_an_object_store_error_is_fatal(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = build_container(settings)

    def broken(key: str) -> bool:
        raise OSError(f"connection refused while reading {key}\nsecond line")

    monkeypatch.setattr(container.layout.store, "exists", broken)
    with pytest.raises(SettingsError, match="connection refused") as raised:
        await container.migrate_user_settings()
    assert "second line" not in str(raised.value)
    await container.aclose()
