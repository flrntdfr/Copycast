"""Public Artwork under ``/art/{asset_id}-{mac}.{ext}``, in both auth modes (U1).

The token is the asset id plus a MAC under the instance secret, so nobody can compute an
artwork URL from a Source URL. Every failure answers the same 404, and the MAC is checked
before anything is looked up.
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from copycast.adapters.db.uow import uow_of
from copycast.app import Container, build_container
from copycast.domain.credentials import artwork_mac, new_instance_secret
from copycast.settings import Settings, get_settings
from copycast.worker.runner import Runner
from tests.conftest import TEST_ALLOW_PRIVATE_HOSTS, TEST_BASE_URL
from tests.integration.api.conftest import Source, archived_items, create_mirror
from tests.support.fake_engine import FakeEngine
from tests.support.paths import FEED_URL
from tests.support.users import session_headers

pytestmark = pytest.mark.integration

OPERATOR = ("florent", "correct-horse-battery")
ART_RE = re.compile(r'href="http://testserver(/art/[^"]+)"')


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(params=["auth-off", "auth-on"])
def settings(
    request: pytest.FixtureRequest,
    store: Any,
    storage_config: dict[str, Any],
    scratch_dir: Path,
    db: str,
) -> Settings:
    """One code path: every test here runs with authentication off and on."""
    extra: dict[str, Any] = {}
    if request.param == "auth-on":
        extra["auth"] = {"username": OPERATOR[0], "password": OPERATOR[1]}
    return get_settings(
        base_url=TEST_BASE_URL,
        storage={**storage_config, "scratch_dir": scratch_dir},
        database_url=db,
        refresh={"interval_hours": 24, "fetch_cooldown_minutes": 15, "concurrency": 1},
        security={"allow_private_hosts": TEST_ALLOW_PRIVATE_HOSTS},
        **extra,
    )


def operator(settings: Settings) -> dict[str, str]:
    return basic(*OPERATOR) if settings.auth.enabled else {}


async def archived_with_art(
    client: httpx.AsyncClient, source: Source, runner: Runner, settings: Settings, name: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if settings.auth.enabled:  # /api takes a session; the operator pair opens the feeds
        client.headers.update(await session_headers(client, *OPERATOR))
    mirror = await create_mirror(client, source.write_rss(name, items=[1], assets=True))
    (item,) = await archived_items(client, runner, mirror["id"])
    client.headers.pop("Cookie", None)
    return mirror, item


def path_of(url: str) -> str:
    return url.removeprefix("http://testserver")


def problem_without_instance(response: httpx.Response) -> dict[str, Any]:
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    body.pop("instance", None)
    return body


async def test_feeds_and_responses_publish_artwork_under_art(
    client: httpx.AsyncClient, source: Source, runner: Runner, settings: Settings
) -> None:
    mirror, item = await archived_with_art(client, source, runner, settings, "art-pub")
    document = await client.get(FEED_URL(mirror["id"]), headers=operator(settings))
    assert document.status_code == 200
    published = ART_RE.findall(document.text)
    (item_art,) = [a for a in item["assets"] if a["kind"] == "artwork"]
    assert path_of(item_art["url"]) in published and len(set(published)) == 2
    assert ".artwork." not in document.text  # no feed id nor item id names an image
    # The feed's channel image and the Episode's image, bare, Range and HEAD included.
    for path in set(published):
        assert re.fullmatch(r"/art/[0-9a-f]{16}-[a-z2-7]{26}\.jpg", path), path
        full = await client.get(path)
        assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
        assert full.headers["cache-control"] == "public, max-age=86400"
        assert full.headers["etag"] and full.headers["accept-ranges"] == "bytes"
        partial = await client.get(path, headers={"Range": "bytes=0-3"})
        assert partial.status_code == 206 and partial.content == full.content[:4]
        head = await client.head(path)
        assert head.status_code == 200 and head.content == b""
    # The same image as the feed's own asset path (open while auth is off, ADR 0004).
    old = await client.get(
        f"/feeds/{mirror['id']}/assets/{item['id']}.artwork.jpg", headers=operator(settings)
    )
    assert old.status_code == 200
    assert old.content == (await client.get(path_of(item_art["url"]))).content
    if not settings.auth.enabled:
        bare = await client.get(f"/feeds/{mirror['id']}/assets/{item['id']}.artwork.jpg")
        assert bare.status_code == 200


async def test_art_answers_one_404_for_every_failure(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    settings: Settings,
    container: Container,
) -> None:
    _, item = await archived_with_art(client, source, runner, settings, "art-404")
    secret = await container.artwork_secret()
    (art,) = [a for a in item["assets"] if a["kind"] == "artwork"]
    (chapters,) = [a for a in item["assets"] if a["kind"] == "chapters"]
    art_id, chapters_id, unknown_id = art["id"], chapters["id"], "f" * 16
    mac = artwork_mac(secret, art_id)
    good = f"/art/{art_id}-{mac}.jpg"
    assert path_of(art["url"]) == good
    assert (await client.get(good)).status_code == 200

    def token(asset_id: str, ext: str = "jpg") -> str:
        return f"/art/{asset_id}-{artwork_mac(secret, asset_id)}.{ext}"

    failures = [
        "/art/nothing",
        "/art/nothing.jpg",
        f"/art/{art_id}-{mac}",  # no extension
        f"/art/{art_id}{mac}.jpg",  # no dash
        f"/art/{art_id}-{mac.upper()}.jpg",
        f"/art/{art_id}-{mac[:-1]}.jpg",
        f"/art/{art_id}-{mac}a.jpg",
        f"/art/{art_id.upper()}-{mac}.jpg",
        f"/art/{art_id}-{mac}.JPG",
        f"/art/{art_id}-{mac}.jpgjpgjpg",
        f"/art/{art_id}-{artwork_mac(secret, unknown_id)}.jpg",  # another id's MAC
        f"/art/{art_id}-{artwork_mac(new_instance_secret(), art_id)}.jpg",  # another secret
        token(unknown_id),  # a valid MAC for an asset that does not exist
        token(chapters_id),  # a valid MAC for an asset that is not artwork
        token(chapters_id, "json"),
        token(art_id, "png"),  # the right asset under another extension
    ]
    reference = await client.get(token(unknown_id))
    assert reference.status_code == 404
    expected = problem_without_instance(reference)
    assert expected["type"] == "urn:copycast:problem:not-found"
    for path in failures:
        for method in ("GET", "HEAD"):
            response = await client.request(method, path)
            assert response.status_code == 404, (method, path, response.text)
            assert "www-authenticate" not in response.headers
            if method == "GET":
                assert problem_without_instance(response) == expected, path

    # An artwork row that is no longer archived, or whose object is gone, answers the same.
    async with container.db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE assets SET state = 'failed' WHERE id = :id"), {"id": art_id}
        )
    gone = await client.get(good)
    assert gone.status_code == 404 and problem_without_instance(gone) == expected
    async with container.db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE assets SET state = 'archived' WHERE id = :id"), {"id": art_id}
        )
    assert (await client.get(good)).status_code == 200
    async with uow_of(container.uow_factory()) as uow:
        row = await uow.assets.get(art_id)
        assert row is not None and row.local_path
        key = container.layout.resolve(row.feed_id, row.local_path)
    container.layout.store.delete(key)
    missing = await client.get(good)
    assert missing.status_code == 404 and problem_without_instance(missing) == expected


async def test_art_checks_the_mac_before_any_lookup(
    client: httpx.AsyncClient,
    source: Source,
    runner: Runner,
    settings: Settings,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, item = await archived_with_art(client, source, runner, settings, "art-mac")
    (art,) = [a for a in item["assets"] if a["kind"] == "artwork"]
    real = container.uow_factory
    opened: list[str] = []

    class Counting:
        def __call__(self) -> Any:
            opened.append("uow")
            return real()

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

    store = container.layout.store
    heads: list[str] = []
    real_head: Callable[[str], Any] = store.head_async

    async def counting_head(key: str) -> Any:
        heads.append(key)
        return await real_head(key)

    monkeypatch.setitem(container._memo, "uow_factory", Counting())  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(store, "head_async", counting_head)
    art_id = art["id"]
    forged = f"/art/{art_id}-{'a' * 26}.jpg"
    assert (await client.get(forged)).status_code == 404
    assert (await client.get("/art/garbage.jpg")).status_code == 404
    assert opened == [] and heads == []  # refused before Postgres or the Object store
    assert (await client.get(path_of(art["url"]))).status_code == 200
    assert opened == ["uow"] and len(heads) == 1


async def test_the_instance_secret_is_minted_once_and_shared(
    settings: Settings, engine: FakeEngine, container: Container
) -> None:
    """Racing processes agree on one secret: INSERT ... ON CONFLICT DO NOTHING, then SELECT."""
    others = [build_container(settings, engine=engine) for _ in range(3)]
    try:
        secrets = await asyncio.gather(
            container.artwork_secret(), *(other.artwork_secret() for other in others)
        )
    finally:
        for other in others:
            await other.aclose()
    assert len(set(secrets)) == 1 and len(secrets[0]) == 32
    async with container.db_engine.connect() as conn:
        rows = (await conn.execute(text("SELECT name, value FROM instance_secrets"))).all()
    assert [(name, bytes(value)) for name, value in rows] == [("artwork", secrets[0])]
    # Cached in-process: a second read does not touch the database again.
    async with container.db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM instance_secrets"))
    assert await container.artwork_secret() == secrets[0]
    # A fresh process on an emptied database mints a new one (``copycast rebuild``).
    fresh = build_container(settings, engine=engine)
    try:
        assert await fresh.artwork_secret() != secrets[0]
    finally:
        await fresh.aclose()
