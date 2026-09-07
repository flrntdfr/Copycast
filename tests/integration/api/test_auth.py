"""Authentication on: the operator pair on the UI and API, a feed's own pair on its routes.

Every fixture here overrides ``settings`` so ``auth.password`` is set; the
rest of the API suite keeps running with authentication off (ADR 0004).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from copycast.adapters.storage.descriptor import FeedDescriptor
from copycast.app import Container
from copycast.settings import Settings, get_settings
from copycast.worker.runner import Runner
from tests.conftest import TEST_BASE_URL
from tests.integration.api.conftest import Source, archived_items, create_mirror
from tests.support.paths import ABOUT_PATH, FEED_URL, HEALTH_LIVE, HEALTH_READY, MEDIA_URL, api

pytestmark = pytest.mark.integration

OPERATOR = ("florent", "correct-horse-battery")
CHALLENGE = 'Basic realm="Copycast", charset="UTF-8"'


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


OPERATOR_HEADERS = basic(*OPERATOR)


@pytest.fixture
def settings(data_dir: Path, db: str) -> Settings:
    """The root ``settings`` with the operator pair set: authentication on."""
    return get_settings(
        base_url=TEST_BASE_URL,
        data_dir=data_dir,
        database_url=db,
        refresh={"interval_hours": 24, "fetch_cooldown_minutes": 15, "concurrency": 1},
        auth={"username": OPERATOR[0], "password": OPERATOR[1]},
    )


def problem(response: httpx.Response) -> dict[str, object]:
    assert response.headers["content-type"].startswith("application/problem+json"), response.text
    body: dict[str, object] = response.json()
    return body


async def test_api_requires_the_operator_pair(client: httpx.AsyncClient) -> None:
    missing = await client.get(api("/feeds"))
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == CHALLENGE
    assert missing.headers["cache-control"] == "no-store"
    assert problem(missing)["type"] == "urn:copycast:problem:unauthorized"
    assert missing.headers["x-request-id"]  # the request-id layer wraps the auth layer

    wrong = await client.get(api("/feeds"), headers=basic("florent", "wrong"))
    assert wrong.status_code == 401 and problem(wrong)["detail"] == "wrong username or password"

    right = await client.get(api("/feeds"), headers=OPERATOR_HEADERS)
    assert right.status_code == 200
    about = await client.get(ABOUT_PATH, headers=OPERATOR_HEADERS)
    assert about.status_code == 200 and about.json()["auth_enabled"] is True

    # The health checks stay open for probes; SSE is gated like the rest of /api.
    assert (await client.get(HEALTH_LIVE)).status_code == 200
    assert (await client.get(HEALTH_READY)).status_code == 200
    async with client.stream("GET", api("/events")) as events:
        assert events.status_code == 401


async def test_feed_routes_take_the_feed_pair_or_the_operator_pair(
    client: httpx.AsyncClient, source: Source, runner: Runner, container: Container
) -> None:
    url = source.write_rss("private", items=[1], artwork=False)
    client.headers.update(OPERATOR_HEADERS)
    mirror = await create_mirror(client, url)
    other = await create_mirror(client, source.write_rss("other", items=[1], artwork=False))
    client.headers.pop("Authorization")

    pair = mirror["feed_credentials"]
    assert pair is not None and len(pair["username"]) == 8 and len(pair["password"]) == 24
    assert mirror["feed_url"] == (
        f"http://{pair['username']}:{pair['password']}@testserver/feeds/{mirror['id']}.xml"
    )
    listed = await client.get(api("/feeds"), headers=OPERATOR_HEADERS)
    assert listed.json()["feeds"][0]["feed_credentials"] is not None

    feed_path = FEED_URL(mirror["id"])
    missing = await client.get(feed_path)
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == CHALLENGE
    assert problem(missing)["type"] == "urn:copycast:problem:unauthorized"
    assert (await client.head(feed_path)).status_code == 401

    feed_headers = basic(pair["username"], pair["password"])
    assert (await client.get(feed_path, headers=feed_headers)).status_code == 200
    assert (await client.head(feed_path, headers=feed_headers)).status_code == 200
    assert (await client.get(feed_path, headers=OPERATOR_HEADERS)).status_code == 200
    # One feed's pair opens nothing else: not another feed, not the API.
    assert (await client.get(FEED_URL(other["id"]), headers=feed_headers)).status_code == 401
    assert (await client.get(api("/feeds"), headers=feed_headers)).status_code == 401
    wrong = await client.get(feed_path, headers=basic(pair["username"], "nope"))
    assert wrong.status_code == 401 and "wrong" in str(problem(wrong)["detail"])
    unknown = await client.get(FEED_URL("no-such-feed"), headers=feed_headers)
    assert unknown.status_code == 401

    # The rendered feed's enclosure URLs stay bare: clients reuse the feed's pair.
    client.headers.update(OPERATOR_HEADERS)
    archived = await archived_items(client, runner, mirror["id"])
    client.headers.pop("Authorization")
    assert len(archived) == 1
    document = await client.get(feed_path, headers=feed_headers)
    assert f"http://testserver/feeds/{mirror['id']}/media/" in document.text
    assert pair["password"] not in document.text

    media_path = MEDIA_URL(mirror["id"], archived[0]["id"], archived[0]["media"]["ext"])
    assert (await client.get(media_path)).status_code == 401
    partial = await client.get(media_path, headers={**feed_headers, "Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert (await client.get(media_path, headers=OPERATOR_HEADERS)).status_code == 200


async def test_artwork_logo_and_robots_are_public(
    client: httpx.AsyncClient, source: Source, runner: Runner
) -> None:
    url = source.write_rss("art", items=[1], artwork=True, assets=True)
    client.headers.update(OPERATOR_HEADERS)
    mirror = await create_mirror(client, url)
    archived = await archived_items(client, runner, mirror["id"])
    client.headers.pop("Authorization")
    # Podcast apps fetch images bare: feed and Episode artwork answer without credentials.
    assert (await client.get(f"/feeds/{mirror['id']}/assets/feed.artwork.jpg")).status_code == 200
    item_art = next(a for a in archived[0]["assets"] if a["kind"] == "artwork")
    assert (await client.get(item_art["url"])).status_code == 200
    # Other assets keep the feed's pair.
    chapters = next(a for a in archived[0]["assets"] if a["kind"] == "chapters")
    assert (await client.get(chapters["url"])).status_code == 401
    # The logo an Inbox feed shows, and robots.txt, are open too.
    logo = await client.get("/feeds/copycast-artwork.png")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
    robots = await client.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /" in robots.text
    assert robots.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert (await client.get(api("/feeds"))).headers["x-robots-tag"].startswith("noindex")


async def test_rotate_feed_credentials(
    client: httpx.AsyncClient, source: Source, container: Container
) -> None:
    client.headers.update(OPERATOR_HEADERS)
    mirror = await create_mirror(client, source.write_rss("rotate", items=[1], artwork=False))
    old = mirror["feed_credentials"]
    rotated = await client.post(api(f"/feeds/{mirror['id']}/credentials/rotate"))
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["feed_credentials"]
    assert new != old and new["username"] != old["username"]
    assert rotated.json()["feed_url"].startswith(f"http://{new['username']}:{new['password']}@")
    client.headers.pop("Authorization")

    feed_path = FEED_URL(mirror["id"])
    assert (
        await client.get(feed_path, headers=basic(old["username"], old["password"]))
    ).status_code == 401
    assert (
        await client.get(feed_path, headers=basic(new["username"], new["password"]))
    ).status_code == 200

    # The descriptor carries the new pair, so a rebuild keeps every subscription working.
    descriptor = FeedDescriptor.read(container.layout.descriptor_path(mirror["id"]))
    assert (descriptor.feed.auth_username, descriptor.feed.auth_password) == (
        new["username"],
        new["password"],
    )
    missing = await client.post(api("/feeds/nope/credentials/rotate"), headers=OPERATOR_HEADERS)
    assert missing.status_code == 404


async def test_api_keys_lifecycle(client: httpx.AsyncClient) -> None:
    client.headers.update(OPERATOR_HEADERS)
    created = await client.post(api("/keys"), json={"name": "  Claude Code  ", "scope": "read"})
    assert created.status_code == 201, created.text
    payload = created.json()
    secret = payload["secret"]
    assert secret.startswith("cck_") and len(secret) == 44
    key = payload["key"]
    assert key["name"] == "Claude Code" and key["scope"] == "read"
    assert key["prefix"] == secret[:12] and key["last_used_at"] is None

    default_scope = await client.post(api("/keys"), json={"name": "laptop"})
    assert default_scope.status_code == 201 and default_scope.json()["key"]["scope"] == "write"

    listed = await client.get(api("/keys"))
    assert listed.status_code == 200
    keys = listed.json()["keys"]
    assert [k["name"] for k in keys] == ["laptop", "Claude Code"]  # newest first
    assert all("secret" not in k for k in keys)

    revoked = await client.delete(api(f"/keys/{key['id']}"))
    assert revoked.status_code == 204
    assert (await client.delete(api(f"/keys/{key['id']}"))).status_code == 404
    assert [k["name"] for k in (await client.get(api("/keys"))).json()["keys"]] == ["laptop"]

    invalid = await client.post(api("/keys"), json={"name": "", "scope": "read"})
    assert invalid.status_code == 422
    bad_scope = await client.post(api("/keys"), json={"name": "x", "scope": "admin"})
    assert bad_scope.status_code == 422
    assert json.loads(invalid.text)["type"] == "urn:copycast:problem:validation"
