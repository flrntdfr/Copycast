"""``/api/engine/cookies``: store, describe and remove the Engine's cookie files."""

from __future__ import annotations

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

    store = container.layout.store
    file = container.layout.cookie_file_key("youtube-cookies.txt")
    jar = container.layout.cookies_key()
    assert store.get_text(file) == COOKIES + "\n"
    assert "secret-value" in (store.get_text(jar) or "")

    rejected = await client.put(api("/engine/cookies"), json={"content": "SID=abc; HSID=def"})
    assert rejected.status_code == 422
    assert rejected.json()["type"] == "urn:copycast:problem:invalid-cookies"
    assert store.get_text(file) == COOKIES + "\n"  # a rejected file changes nothing

    # A second file adds a row; the jar the Engine reads carries both.
    second = await client.put(api("/engine/cookies"), json={"content": TWITCH, "name": "twitch"})
    body = second.json()
    assert [file["name"] for file in body["files"]] == ["twitch", "youtube-cookies"]
    assert _services(body)["twitch"] is True and body["cookie_count"] == 4
    assert "auth-token" in (store.get_text(jar) or "")

    # The Engine rotated a cookie in the jar: removing another file keeps the rotation.
    store.put_bytes(jar, (store.get_text(jar) or "").replace("secret-value", "rotated"))
    assert (
        await client.delete(api("/engine/cookies"), params={"name": "twitch"})
    ).status_code == 204
    assert "rotated" in (store.get_text(file) or "")
    assert "auth-token" not in (store.get_text(jar) or "")
    missing = await client.delete(api("/engine/cookies"), params={"name": "twitch"})
    assert missing.status_code == 404

    removed = await client.delete(api("/engine/cookies"))
    assert removed.status_code == 204 and not store.exists(jar) and not store.exists(file)
    assert (await client.delete(api("/engine/cookies"))).status_code == 204
    assert (await client.get(api("/engine/cookies"))).json()["present"] is False


async def test_a_jar_stored_before_files_existed_shows_as_one_file(
    client: httpx.AsyncClient, container: Container
) -> None:
    store = container.layout.store
    jar = container.layout.cookies_key()
    store.put_bytes(jar, COOKIES + "\n")
    body = (await client.get(api("/engine/cookies"))).json()
    assert [file["name"] for file in body["files"]] == ["cookies"]
    assert _services(body)["youtube"] is True

    await client.put(api("/engine/cookies"), json={"content": TWITCH, "name": "twitch"})
    names = sorted(o.name for o in store.list(container.layout.cookie_files_prefix()))
    assert names == ["cookies.txt", "twitch.txt"]
    assert "secret-value" in (store.get_text(jar) or "")
