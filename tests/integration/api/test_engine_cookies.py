"""``/api/engine/cookies``: store, describe and remove the Engine's cookie files."""

from __future__ import annotations

import stat

import httpx
import pytest

from copycast.app import Container
from tests.support.paths import api

pytestmark = pytest.mark.integration

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tsecret-value\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1790000000\tHSID\tanother\n"
    "soundcloud.com\tFALSE\t/\tFALSE\t0\toauth_token\ty"
)
TWITCH = ".twitch.tv\tTRUE\t/\tTRUE\t1790000000\tauth-token\tt\n"


def _services(body: dict[str, object]) -> dict[str, bool]:
    services = body["services"]
    assert isinstance(services, list)
    return {row["id"]: row["authenticated"] for row in services}  # type: ignore[index]


async def test_cookies_lifecycle(client: httpx.AsyncClient, container: Container) -> None:
    absent = await client.get(api("/engine/cookies"))
    assert absent.status_code == 200
    body = absent.json()
    assert body["present"] is False and body["files"] == []
    assert _services(body) == {"youtube": False, "twitch": False, "vimeo": False, "mixcloud": False}

    stored = await client.put(
        api("/engine/cookies"), json={"content": COOKIES, "name": "YouTube cookies.txt"}
    )
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["present"] is True and body["youtube"] is True
    assert body["cookie_count"] == 3 and body["domains"] == ["soundcloud.com", "youtube.com"]
    assert [file["name"] for file in body["files"]] == ["youtube-cookies"]
    assert body["files"][0]["services"] == ["youtube"]
    assert _services(body)["youtube"] is True and _services(body)["twitch"] is False
    assert "secret-value" not in stored.text

    file = container.layout.cookie_files_dir() / "youtube-cookies.txt"
    jar = container.layout.cookies_path()
    assert file.read_text(encoding="utf-8") == COOKIES + "\n"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600
    assert "secret-value" in jar.read_text(encoding="utf-8")

    rejected = await client.put(api("/engine/cookies"), json={"content": "SID=abc; HSID=def"})
    assert rejected.status_code == 422
    assert rejected.json()["type"] == "urn:copycast:problem:invalid-cookies"
    assert file.read_text(encoding="utf-8") == COOKIES + "\n"  # a rejected file changes nothing

    # A second file adds a row; the jar the Engine reads carries both.
    second = await client.put(api("/engine/cookies"), json={"content": TWITCH, "name": "twitch"})
    body = second.json()
    assert [file["name"] for file in body["files"]] == ["twitch", "youtube-cookies"]
    assert _services(body)["twitch"] is True and body["cookie_count"] == 4
    assert "auth-token" in jar.read_text(encoding="utf-8")

    # The Engine rotated a cookie in the jar: removing another file keeps the rotation.
    jar.write_text(jar.read_text(encoding="utf-8").replace("secret-value", "rotated"))
    assert (
        await client.delete(api("/engine/cookies"), params={"name": "twitch"})
    ).status_code == 204
    assert "rotated" in file.read_text(encoding="utf-8")
    assert "auth-token" not in jar.read_text(encoding="utf-8")
    missing = await client.delete(api("/engine/cookies"), params={"name": "twitch"})
    assert missing.status_code == 404

    removed = await client.delete(api("/engine/cookies"))
    assert removed.status_code == 204 and not jar.exists() and not file.exists()
    assert (await client.delete(api("/engine/cookies"))).status_code == 204
    assert (await client.get(api("/engine/cookies"))).json()["present"] is False


async def test_a_jar_stored_before_files_existed_shows_as_one_file(
    client: httpx.AsyncClient, container: Container
) -> None:
    jar = container.layout.cookies_path()
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_text(COOKIES + "\n", encoding="utf-8")
    body = (await client.get(api("/engine/cookies"))).json()
    assert [file["name"] for file in body["files"]] == ["cookies"]
    assert _services(body)["youtube"] is True

    await client.put(api("/engine/cookies"), json={"content": TWITCH, "name": "twitch"})
    names = sorted(path.name for path in container.layout.cookie_files_dir().iterdir())
    assert names == ["cookies.txt", "twitch.txt"]
    assert "secret-value" in jar.read_text(encoding="utf-8")
