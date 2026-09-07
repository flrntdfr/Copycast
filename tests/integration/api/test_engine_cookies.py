"""``/api/engine/cookies``: store, describe and remove the Engine's cookie file."""

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


async def test_cookies_lifecycle(client: httpx.AsyncClient, container: Container) -> None:
    absent = await client.get(api("/engine/cookies"))
    assert absent.status_code == 200
    assert absent.json() == {
        "present": False,
        "size_bytes": 0,
        "updated_at": None,
        "cookie_count": 0,
        "domains": [],
        "youtube": False,
    }

    stored = await client.put(api("/engine/cookies"), json={"content": COOKIES})
    assert stored.status_code == 200, stored.text
    body = stored.json()
    assert body["present"] is True and body["youtube"] is True
    assert body["cookie_count"] == 3 and body["domains"] == ["soundcloud.com", "youtube.com"]
    assert body["updated_at"] is not None and body["size_bytes"] > 0
    assert "secret-value" not in stored.text

    path = container.layout.cookies_path()
    assert path.is_file() and path.read_text(encoding="utf-8") == COOKIES + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (await client.get(api("/engine/cookies"))).json()["cookie_count"] == 3

    rejected = await client.put(api("/engine/cookies"), json={"content": "SID=abc; HSID=def"})
    assert rejected.status_code == 422
    assert rejected.json()["type"] == "urn:copycast:problem:invalid-cookies"
    assert path.read_text(encoding="utf-8") == COOKIES + "\n"  # a rejected file changes nothing

    removed = await client.delete(api("/engine/cookies"))
    assert removed.status_code == 204 and not path.exists()
    assert (await client.delete(api("/engine/cookies"))).status_code == 204
    assert (await client.get(api("/engine/cookies"))).json()["present"] is False
