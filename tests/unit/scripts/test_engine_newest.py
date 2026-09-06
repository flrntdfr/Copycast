from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from packaging.version import Version
from scripts import engine_newest


def _file(yanked: bool = False) -> dict[str, Any]:
    return {"filename": "yt_dlp.whl", "yanked": yanked}


RELEASES: dict[str, Any] = {
    "2026.8.19": [_file()],
    "2026.7.1": [_file()],
    "2026.8.19.232908.dev0": [_file()],
    "2026.9.2.101010.dev0": [_file()],
    "2026.9.3.111111.dev0": [_file(yanked=True)],  # every file yanked
    "2026.9.4": [],  # no files at all
    "2026.9.1a1": [_file()],
    "not-a-version": [_file()],
}


def test_stable_ignores_pre_releases_and_unusable_releases() -> None:
    assert engine_newest.newest_version(RELEASES, "stable") == Version("2026.8.19")


def test_nightly_includes_pre_releases_but_not_yanked_ones() -> None:
    assert engine_newest.newest_version(RELEASES, "nightly") == Version("2026.9.2.101010.dev0")


def test_no_usable_release_raises() -> None:
    with pytest.raises(engine_newest.NoRelease):
        engine_newest.newest_version({"2026.9.4": [], "x": [_file()]}, "stable")
    with pytest.raises(engine_newest.NoRelease):
        engine_newest.newest_version({"2026.9.1a1": [_file()]}, "stable")


@respx.mock
def test_fetch_releases_reads_the_pypi_json_document() -> None:
    route = respx.get(engine_newest.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"releases": RELEASES})
    )
    releases = engine_newest.fetch_releases()
    assert route.called
    assert route.calls.last.request.headers["accept"] == "application/json"
    assert set(releases) == set(RELEASES)


@respx.mock
def test_fetch_releases_rejects_documents_without_releases() -> None:
    respx.get(engine_newest.PYPI_JSON_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(engine_newest.NoRelease):
        engine_newest.fetch_releases()


@respx.mock
def test_main_prints_the_normalized_newest_version(capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(engine_newest.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"releases": {"2026.08.19": [_file()]}})
    )
    assert engine_newest.main(["--channel", "stable"]) == 0
    assert capsys.readouterr().out.strip() == "2026.8.19"


@respx.mock
def test_main_defaults_to_nightly(capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(engine_newest.PYPI_JSON_URL).mock(
        return_value=httpx.Response(200, json={"releases": RELEASES})
    )
    assert engine_newest.main([]) == 0
    assert capsys.readouterr().out.strip() == "2026.9.2.101010.dev0"


@respx.mock
def test_main_fails_cleanly_on_http_errors(capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(engine_newest.PYPI_JSON_URL).mock(return_value=httpx.Response(503))
    assert engine_newest.main(["--channel", "nightly"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "engine_newest:" in captured.err


@respx.mock
def test_main_fails_cleanly_on_network_errors(capsys: pytest.CaptureFixture[str]) -> None:
    respx.get(engine_newest.PYPI_JSON_URL).mock(side_effect=httpx.ConnectError("down"))
    assert engine_newest.main([]) == 1
    assert "down" in capsys.readouterr().err
