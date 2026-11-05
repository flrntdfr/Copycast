"""No existence oracle under ``/feeds`` while authentication is on (U3).

Whoever lacks the right credential gets the one same 401 (status, challenge, headers and
body apart from ``instance``) whatever the feed, item or asset, and whatever was wrong:
nothing tells "this server mirrors channel X" or "this video was archived" apart from a
typo. GET and HEAD alike, for the feed, media and assets, bare and under ``/k/{key}/``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from copycast.domain.credentials import feed_media_key
from copycast.settings import Settings, get_settings
from copycast.worker.runner import Runner
from tests.conftest import TEST_BASE_URL
from tests.integration.api.conftest import Source, archived_items, create_mirror

pytestmark = pytest.mark.integration

OPERATOR = ("florent", "correct-horse-battery")
CHALLENGE = 'Basic realm="Copycast", charset="UTF-8"'
UNKNOWN_FEED = "0123456789abcdef"
UNKNOWN_ITEM = "fedcba9876543210"
COMPARED_HEADERS = ("content-type", "www-authenticate", "cache-control", "x-robots-tag")


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def settings(store: Any, storage_config: dict[str, Any], scratch_dir: Path, db: str) -> Settings:
    return get_settings(
        base_url=TEST_BASE_URL,
        storage={**storage_config, "scratch_dir": scratch_dir},
        database_url=db,
        refresh={"interval_hours": 24, "fetch_cooldown_minutes": 15, "concurrency": 1},
        auth={"username": OPERATOR[0], "password": OPERATOR[1]},
    )


@dataclass(frozen=True)
class World:
    feed_id: str
    item_id: str
    ext: str
    pair: dict[str, str]
    key: str
    other_id: str
    other_pair: dict[str, str]
    other_key: str


@pytest.fixture
async def world(client: httpx.AsyncClient, source: Source, runner: Runner) -> World:
    client.headers.update(basic(*OPERATOR))
    mirror = await create_mirror(client, source.write_rss("oracle", items=[1], assets=True))
    other = await create_mirror(client, source.write_rss("other", items=[1], artwork=False))
    (item,) = await archived_items(client, runner, mirror["id"])
    client.headers.pop("Authorization")
    pair, other_pair = mirror["feed_credentials"], other["feed_credentials"]
    return World(
        feed_id=mirror["id"],
        item_id=item["id"],
        ext=item["media"]["ext"],
        pair=pair,
        key=feed_media_key(mirror["id"], pair["password"]),
        other_id=other["id"],
        other_pair=other_pair,
        other_key=feed_media_key(other["id"], other_pair["password"]),
    )


def cases(w: World) -> list[tuple[str, str, dict[str, str]]]:
    """``(name, path, headers)``: every way to ask without the right credential."""
    feed, item, ext = w.feed_id, w.item_id, w.ext
    own = w.pair
    wrong = basic(own["username"], "x" * 24)
    other = basic(w.other_pair["username"], w.other_pair["password"])
    garbage = {"Authorization": "Basic !!!not-base64"}
    bearer = {"Authorization": "Bearer cck_nothing"}
    media = f"/feeds/{feed}/media/{item}.{ext}"
    chapters = f"/feeds/{feed}/assets/{item}.chapters.json"
    artwork = f"/feeds/{feed}/assets/feed.artwork.jpg"
    keyed = f"/feeds/{feed}/k/{{key}}"
    out: list[tuple[str, str, dict[str, str]]] = []
    for label, headers in (
        ("missing pair", {}),
        ("wrong pair", wrong),
        ("another feed's pair", other),
        ("garbage header", garbage),
        ("bearer", bearer),
    ):
        out += [
            (f"feed.xml, {label}", f"/feeds/{feed}.xml", headers),
            (f"unknown feed.xml, {label}", f"/feeds/{UNKNOWN_FEED}.xml", headers),
            (f"media, {label}", media, headers),
            (f"unknown item, {label}", f"/feeds/{feed}/media/{UNKNOWN_ITEM}.mp3", headers),
            (
                f"unknown feed's media, {label}",
                f"/feeds/{UNKNOWN_FEED}/media/{item}.{ext}",
                headers,
            ),
            (f"malformed media name, {label}", f"/feeds/{feed}/media/nothing", headers),
            (f"asset, {label}", chapters, headers),
            (f"unknown asset, {label}", f"/feeds/{feed}/assets/nothing.json", headers),
            (f"old artwork path, {label}", artwork, headers),
            (
                f"unknown feed's artwork, {label}",
                f"/feeds/{UNKNOWN_FEED}/assets/feed.artwork.jpg",
                headers,
            ),
        ]
    for label, key in (
        ("wrong key", "a" * 22),
        ("another feed's key", w.other_key),
        ("garbage key", "nope"),
    ):
        out += [
            (f"keyed media, {label}", keyed.format(key=key) + f"/media/{item}.{ext}", {}),
            (
                f"keyed unknown item, {label}",
                keyed.format(key=key) + f"/media/{UNKNOWN_ITEM}.mp3",
                {},
            ),
            (f"keyed asset, {label}", keyed.format(key=key) + f"/assets/{item}.chapters.json", {}),
            (f"keyed unknown asset, {label}", keyed.format(key=key) + "/assets/nothing.json", {}),
            (f"keyed artwork, {label}", keyed.format(key=key) + "/assets/feed.artwork.jpg", {}),
        ]
    # The right key, but for a feed id it does not belong to (or that does not exist).
    out += [
        ("key on another feed", f"/feeds/{w.other_id}/k/{w.key}/media/{item}.{ext}", {}),
        ("key on an unknown feed", f"/feeds/{UNKNOWN_FEED}/k/{w.key}/media/{item}.{ext}", {}),
        ("key on an unknown feed's asset", f"/feeds/{UNKNOWN_FEED}/k/{w.key}/assets/x.json", {}),
        ("another feed's pair on keyed", keyed.format(key="a" * 22) + "/assets/x.json", other),
        # A ``?key=`` query never stands in for the path segment.
        ("key as a query", f"{media}?key={w.key}", {}),
    ]
    return out


def shape(response: httpx.Response) -> tuple[int, dict[str, str | None]]:
    return response.status_code, {name: response.headers.get(name) for name in COMPARED_HEADERS}


def body_without_instance(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    body.pop("instance", None)
    return body


async def test_every_refusal_is_the_same_answer(client: httpx.AsyncClient, world: World) -> None:
    reference = await client.get(f"/feeds/{UNKNOWN_FEED}.xml")
    assert reference.status_code == 401
    assert reference.headers["www-authenticate"] == CHALLENGE
    expected_shape = shape(reference)
    expected_body = body_without_instance(reference)
    assert expected_body["type"] == "urn:copycast:problem:unauthorized"
    assert expected_body["detail"] == "this feed requires its username and password"
    matrix = cases(world)
    assert len(matrix) > 60
    for name, path, headers in matrix:
        got = await client.get(path, headers=headers)
        assert shape(got) == expected_shape, (name, got.status_code, got.text)
        assert body_without_instance(got) == expected_body, name
        assert got.json()["instance"] == path.split("?", 1)[0], name
        head = await client.head(path, headers=headers)
        assert shape(head) == expected_shape, (name, "HEAD", head.status_code)
        assert head.content == b"", name


async def test_the_right_credential_still_opens_everything(
    client: httpx.AsyncClient, world: World
) -> None:
    """The control group: each credential reaches the same paths, and only then does a 404
    tell an unknown item apart (to someone who may read the feed anyway)."""
    feed, item, ext = world.feed_id, world.item_id, world.ext
    pair = basic(world.pair["username"], world.pair["password"])
    for headers in (pair, basic(*OPERATOR)):
        assert (await client.get(f"/feeds/{feed}.xml", headers=headers)).status_code == 200
        media = await client.get(f"/feeds/{feed}/media/{item}.{ext}", headers=headers)
        assert media.status_code == 200
        chapters = await client.get(f"/feeds/{feed}/assets/{item}.chapters.json", headers=headers)
        assert chapters.status_code == 200
        unknown = await client.get(f"/feeds/{feed}/media/{UNKNOWN_ITEM}.mp3", headers=headers)
        assert unknown.status_code == 404
    keyed = f"/feeds/{feed}/k/{world.key}"
    assert (await client.get(f"{keyed}/media/{item}.{ext}")).status_code == 200
    assert (await client.head(f"{keyed}/assets/{item}.chapters.json")).status_code == 200
    assert (await client.get(f"{keyed}/assets/feed.artwork.jpg")).status_code == 200
    assert (await client.get(f"{keyed}/media/{UNKNOWN_ITEM}.mp3")).status_code == 404
    # The operator on an unknown feed: a plain 404, past authentication.
    gone = await client.get(f"/feeds/{UNKNOWN_FEED}.xml", headers=basic(*OPERATOR))
    assert gone.status_code == 404


async def test_a_refused_request_looks_nothing_up(
    client: httpx.AsyncClient, world: World, container: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Authentication runs first (a router dependency): a request without any credential is
    refused before the feed, item or asset is looked up, so timing tells nothing either."""
    real = container.uow_factory
    opened: list[str] = []

    class Counting:
        def __call__(self) -> Any:
            opened.append("uow")
            return real()

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

    monkeypatch.setitem(container._memo, "uow_factory", Counting())
    for path in (
        f"/feeds/{world.feed_id}.xml",
        f"/feeds/{UNKNOWN_FEED}.xml",
        f"/feeds/{world.feed_id}/media/{world.item_id}.{world.ext}",
        f"/feeds/{world.feed_id}/assets/feed.artwork.jpg",
    ):
        assert (await client.get(path)).status_code == 401
    assert opened == []
    # With a credential to check, a known and an unknown feed cost one same lookup each.
    wrong = basic(world.pair["username"], "x" * 24)
    assert (await client.get(f"/feeds/{world.feed_id}.xml", headers=wrong)).status_code == 401
    assert (await client.get(f"/feeds/{UNKNOWN_FEED}.xml", headers=wrong)).status_code == 401
    assert opened == ["uow", "uow"]
