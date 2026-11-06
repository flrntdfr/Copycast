"""``/api/engine/cookies``: store, describe and remove the caller's own Cookie files."""

from __future__ import annotations

import httpx
import pytest

from copycast.adapters.db.uow import uow_of
from copycast.app import Container
from copycast.domain.enums import JobTrigger, RefreshRunStatus
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.paths import api
from tests.support.world import ClientFactory, signed_in

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
    # Open mode: the implicit Admin's own files, under users/copycast/.
    file = container.layout.user_cookie_file_key("copycast", "youtube-cookies.txt")
    jar = container.layout.user_cookies_key("copycast")
    assert file == "users/copycast/cookie-files/youtube-cookies.txt"
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
    jar = container.layout.user_cookies_key("copycast")
    store.put_bytes(jar, COOKIES + "\n")
    body = (await client.get(api("/engine/cookies"))).json()
    assert [file["name"] for file in body["files"]] == ["cookies"]
    assert _services(body)["youtube"] is True

    await client.put(api("/engine/cookies"), json={"content": TWITCH, "name": "twitch"})
    names = sorted(
        o.name for o in store.list(container.layout.user_cookie_files_prefix("copycast"))
    )
    assert names == ["cookies.txt", "twitch.txt"]
    assert "secret-value" in (store.get_text(jar) or "")


# ---------------------------------------------------------------------- per user (3.0, D5)


async def test_each_user_has_their_own_cookie_files(
    users_container: Container, users_client_factory: ClientFactory
) -> None:
    store = users_container.layout.store
    layout = users_container.layout
    async with (
        signed_in(users_client_factory, "alice") as alice,
        signed_in(users_client_factory, "bob") as bob,
        signed_in(users_client_factory, "root") as root,
    ):
        stored = await alice.put(api("/engine/cookies"), json={"content": COOKIES, "name": "yt"})
        assert stored.status_code == 200, stored.text
        assert "secret-value" not in stored.text
        assert store.get_text(layout.user_cookie_file_key("alice", "yt.txt")) == COOKIES + "\n"
        assert "secret-value" in (store.get_text(layout.user_cookies_key("alice")) or "")
        assert not store.exists(layout.cookies_key())  # the legacy jar is never written

        # Bob and the Admin see only their own (none), and never Alice's contents.
        for other in (bob, root):
            body = (await other.get(api("/engine/cookies"))).json()
            assert body["present"] is False and body["files"] == []
            assert _services(body)["youtube"] is False

        # Bob's file and Bob's delete-all leave Alice's alone.
        await bob.put(api("/engine/cookies"), json={"content": TWITCH, "name": "twitch"})
        assert (await bob.delete(api("/engine/cookies"))).status_code == 204
        assert (await bob.delete(api("/engine/cookies"), params={"name": "yt"})).status_code == 404
        assert not store.exists(layout.user_cookies_key("bob"))
        alices = (await alice.get(api("/engine/cookies"))).json()
        assert [file["name"] for file in alices["files"]] == ["yt"]
        assert store.exists(layout.user_cookies_key("alice"))


async def test_status_crawlers_reflect_the_callers_own_cookie_files(
    users_container: Container, users_client_factory: ClientFactory
) -> None:
    engine = users_container.engine
    assert isinstance(engine, FakeEngine)
    url = "https://www.youtube.com/@alices/videos"
    engine.script_listing(url, listing(2, service="YouTube", title="Alice's channel"))
    async with (
        signed_in(users_client_factory, "alice") as alice,
        signed_in(users_client_factory, "bob") as bob,
        signed_in(users_client_factory, "root") as root,
    ):
        created = await alice.post(
            api("/mirrors"), json={"source_url": url, "backfill": {"mode": "new"}}
        )
        assert created.status_code == 201, created.text
        async with uow_of(users_container.uow_factory()) as uow:
            run = await uow.telemetry.start_refresh_run(
                created.json()["id"], trigger=JobTrigger.scheduled
            )
            await uow.telemetry.finish_refresh_run(run.id, RefreshRunStatus.succeeded)
        await alice.put(api("/engine/cookies"), json={"content": COOKIES, "name": "yt"})

        async def youtube_fact(client: httpx.AsyncClient) -> str | None:
            status = (await client.get(api("/status"))).json()
            crawlers = next(c for c in status["components"] if c["id"] == "crawlers")
            facts = {fact["label"]: fact["value"] for fact in crawlers.get("facts") or []}
            return facts.get("YouTube")

        assert (await youtube_fact(alice) or "").endswith("· cookies")
        # Bob and the Admin hold no YouTube cookies: their rows (if they see Alice's
        # Refresh at all) never say the service is unlocked.
        for other in (bob, root):
            assert "cookies" not in (await youtube_fact(other) or "")
