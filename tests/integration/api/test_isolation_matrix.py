"""The isolation matrix: every ``/api`` route, as Alice, Bob's Admin and API keys, on Bob's things.

The routes come from the app's OpenAPI document and their ``Access`` from the capability
registry (``application.capabilities.ACCESS``):

- ``owner_of_feed``: Bob's ids replace the path parameters and ``SAMPLE_BODIES`` gives a
  minimal body. Alice gets 404, never 403; root (an Admin session) never gets 404; an
  API key, Alice's or root's, gets 404 on a tool capability's route (a key never widens
  its owner's Feeds) and 403 on the others (they refuse keys).
- ``member``: Alice's answers never carry any of Bob's ids, titles or his Source URL;
  for ``get_status`` that includes the ``crawlers`` component, where Bob's failed Refresh
  must show for root only.
- ``admin``: Alice gets 403 ``forbidden``.
- capability-less ``/api`` routes are listed by hand in ``CAPABILITY_LESS``; the guard
  test fails when OpenAPI has one the list misses, so nothing slips through.

The test fails when an ``owner_of_feed`` route has no sample body or a path parameter
with no id mapping.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from copycast.application.capabilities import ACCESS, Access, tool_capabilities
from tests.integration.conftest import running, settings_with
from tests.support.audio import TINY_MP3
from tests.support.users import login, users_auth
from tests.support.world import World

pytestmark = pytest.mark.integration

HTTP_METHODS = ("get", "put", "post", "patch", "delete")

SAMPLE_BODIES: dict[str, dict[str, Any] | None] = {
    "get_feed": None,
    "delete_feed": None,
    "clear_feed": {"dry_run": True},
    "rotate_feed_credentials": None,
    "list_feed_objects": None,
    "list_items": None,
    "get_item": None,
    "archive_item": None,
    "delete_item": None,
    "fetch_item_metadata": None,
    "set_archive_flag": {"archive_flag": True},
    "update_mirror": {"title": "Renamed by someone"},
    "set_paused": None,
    "request_refresh": None,
    "select_items": {"selection": "1"},
    "preview_mirror_update": {"title": "Renamed by someone"},
    "archive_available": None,
    "retry_failed": None,
    "update_bucket": {"name": "Renamed by someone"},
    "add_request": {"url": "https://www.youtube.com/watch?v=someoneelse"},
    "list_requests": None,
    "get_request": None,
    "prune_bucket": {"older_than_days": 36500, "dry_run": True},
    "add_uploads": {"upload_ids": ["{upload_id}"]},
    "get_job": None,
    "cancel_job": None,
}
"""A minimal body per ``owner_of_feed`` capability (None: no body); ``{upload_id}`` stands
for a received Upload of the caller's own (Bob's for
root: an Admin may add an Upload only into a Feed of the Upload's Owner, spec §3.8)."""

ADMIN_BODIES: dict[str, dict[str, Any]] = {
    "rebuild": {"dry_run": True},
    "purge_episodes": {"dry_run": True},
}


@dataclass(frozen=True, slots=True)
class Route:
    method: str
    path: str
    operation_id: str
    capability: str | None

    def __str__(self) -> str:
        return f"{self.method.upper()} {self.path} ({self.operation_id})"


CAPABILITY_LESS: dict[str, str] = {
    "list_mirrors": "member",
    "list_buckets": "member",
    "download_feed_object": "owner_of_feed",
    "resume_mirror": "owner_of_feed",
}
"""Capability-less ``/api`` routes by operation id, and how the matrix treats each.

(The SSE ``?feed_id=`` case is ``subscribe_events``'s, in ``test_events_scoping.py``.)"""

CAPABILITY_LESS_BODIES: dict[str, dict[str, Any] | None] = {
    "download_feed_object": None,
    "resume_mirror": None,
}

DESTRUCTIVE_LAST: tuple[str, ...] = (
    "clear_feed",
    "prune_bucket",
    "delete_item",
    "cancel_job",
    "delete_feed",
)
"""root runs these after everything else, ``delete_feed`` last: they change Bob's world."""


def api_routes(app: Any) -> list[Route]:
    """Every ``/api`` operation of the app's OpenAPI document."""
    routes: list[Route] = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method, operation in operations.items():
            if method in HTTP_METHODS:
                routes.append(
                    Route(method, path, operation["operationId"], operation.get("x-capability"))
                )
    return routes


def access_of(route: Route) -> str:
    if route.capability is not None:
        return ACCESS[route.capability].value
    return CAPABILITY_LESS[route.operation_id]


def bob_ids(world: World) -> dict[str, str]:
    """Bob's ids by path parameter (``feed_id`` is chosen per route by :func:`concrete`)."""
    return {
        "item_id": world.item_id,
        "request_id": world.request_id,
        "job_id": world.job_id,
        "bucket_id": world.bucket_id,
        "path": "feed.json",
    }


GHOST_REQUEST = str(uuid.uuid4())
GHOST_JOB = str(uuid.uuid4())


def ghost_ids() -> dict[str, str]:
    """Ids of the same shapes as Bob's that nothing has: the answer for them is the baseline
    a hidden Feed's answer must match (no existence oracle)."""
    return {
        "feed_id": "0123456789abcdef",
        "item_id": "fedcba9876543210",
        "request_id": GHOST_REQUEST,
        "job_id": GHOST_JOB,
        "bucket_id": "nobody-s-later-abcdef",
        "path": "feed.json",
    }


def concrete(route: Route, ids: dict[str, str], world: World | None = None) -> str:
    """``route.path`` with ``ids``: the Local feed for item and object routes, the Mirror for
    the others (``world`` given: Bob's)."""
    values = dict(ids)
    on_bucket = "{item_id}" in route.path or "{path}" in route.path
    if world is not None:
        values["feed_id"] = world.bucket_id if on_bucket else world.mirror_id
    elif on_bucket:
        values["feed_id"] = values["bucket_id"]
    missing = [name for name in re.findall(r"{(\w+)}", route.path) if name not in values]
    assert not missing, f"{route}: no id mapping for {missing}"
    return re.sub(r"{(\w+)}", lambda match: values[match.group(1)], route.path)


def body_for(route: Route, upload_id: str) -> dict[str, Any] | None:
    if route.capability is None:
        body = CAPABILITY_LESS_BODIES[route.operation_id]
    else:
        assert route.capability in SAMPLE_BODIES, f"{route}: add a sample body"
        body = SAMPLE_BODIES[route.capability]
    if body is None:
        return None

    def fill(value: Any) -> Any:
        if value == "{upload_id}":
            return upload_id
        if isinstance(value, list):
            return [fill(entry) for entry in value]  # type: ignore[misc]
        return value

    return {key: fill(value) for key, value in body.items()}


async def send(
    client: httpx.AsyncClient,
    route: Route,
    world: World,
    upload_id: str,
    headers: dict[str, str] | None = None,
    *,
    ghost: bool = False,
) -> httpx.Response:
    path = concrete(route, ghost_ids()) if ghost else concrete(route, bob_ids(world), world)
    return await client.request(
        route.method.upper(), path, json=body_for(route, upload_id), headers=headers
    )


def normalised(response: httpx.Response, ids: list[str]) -> tuple[int, str, str]:
    """The status, problem type and detail of a 404, with ``ids`` blanked out."""
    body: dict[str, Any] = response.json()
    detail = str(body.get("detail", ""))
    for ident in ids:
        detail = detail.replace(ident, "<id>")
    return response.status_code, str(body.get("type")), detail


async def received_upload(client: httpx.AsyncClient, name: str) -> str:
    """Stage an Upload as ``client``'s User and PUT ``tiny.mp3`` into it; its id."""
    staged = await client.post("/api/uploads", json={"filename": f"{name}.mp3"})
    assert staged.status_code == 201, staged.text
    put = await client.put(
        urlsplit(staged.json()["upload_url"]).path, content=TINY_MP3.read_bytes()
    )
    assert put.status_code == 200, put.text
    upload_id: str = staged.json()["id"]
    return upload_id


def owned_routes(app: Any) -> list[Route]:
    routes = [route for route in api_routes(app) if access_of(route) == Access.owner_of_feed]

    def rank(route: Route) -> int:
        if route.capability in DESTRUCTIVE_LAST:
            return DESTRUCTIVE_LAST.index(route.capability) + 1
        return 0

    return sorted(routes, key=rank)


async def mint_key(client: httpx.AsyncClient) -> dict[str, str]:
    created = await client.post("/api/keys", json={"name": "matrix", "scope": "full"})
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['secret']}"}


# --------------------------------------------------------------------------- guards


def test_every_capability_less_api_route_is_listed(users_app: Any) -> None:
    unlisted = [
        str(route)
        for route in api_routes(users_app)
        if route.capability is None and route.operation_id not in CAPABILITY_LESS
    ]
    assert not unlisted, f"add these capability-less /api routes to the matrix: {unlisted}"


def test_every_owner_of_feed_capability_is_routed_and_has_a_sample(users_app: Any) -> None:
    routed = {route.capability for route in api_routes(users_app)}
    owned = {name for name, access in ACCESS.items() if access is Access.owner_of_feed}
    assert owned <= routed
    assert owned == set(SAMPLE_BODIES), "SAMPLE_BODIES must list every owner_of_feed capability"


# --------------------------------------------------------------------------- owner_of_feed


async def test_alice_gets_404_on_every_route_of_bobs_things(
    world: World, users_app: Any, alice_client: httpx.AsyncClient
) -> None:
    upload_id = await received_upload(alice_client, "alice-part")
    real, fake = list(bob_ids(world).values()), list(ghost_ids().values())
    real += [world.mirror_id]
    failures: list[str] = []
    for route in owned_routes(users_app):
        response = await send(alice_client, route, world, upload_id)
        if response.status_code != 404:
            failures.append(f"{route}: {response.status_code} {response.text[:200]}")
            continue
        baseline = await send(alice_client, route, world, upload_id, ghost=True)
        if normalised(response, real) != normalised(baseline, fake):
            failures.append(f"{route}: {response.text[:200]} differs from {baseline.text[:200]}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("owner", ["alice", "root"])
async def test_api_keys_never_reach_bobs_things(
    world: World,
    users_app: Any,
    users_client: httpx.AsyncClient,
    alice_client: httpx.AsyncClient,
    root_client: httpx.AsyncClient,
    owner: str,
) -> None:
    session = alice_client if owner == "alice" else root_client
    headers = await mint_key(session)
    upload_id = await received_upload(session, f"{owner}-part")
    tools = tool_capabilities()
    failures: list[str] = []
    for route in owned_routes(users_app):
        response = await send(users_client, route, world, upload_id, headers)
        expected = 404 if route.capability in tools else 403
        if response.status_code != expected:
            failures.append(f"{route}: {response.status_code} (wanted {expected})")
    assert not failures, "\n".join(failures)


async def test_root_reaches_every_route_of_bobs_things(
    world: World, users_app: Any, root_client: httpx.AsyncClient, bob_client: httpx.AsyncClient
) -> None:
    upload_id = await received_upload(bob_client, "bob-part-2")
    failures: list[str] = []
    for route in owned_routes(users_app):
        response = await send(root_client, route, world, upload_id)
        if response.status_code == 404 or response.status_code >= 500:
            failures.append(f"{route}: {response.status_code} {response.text[:200]}")
    assert not failures, "\n".join(failures)


async def test_bob_reaches_his_own_things(
    world: World, users_app: Any, bob_client: httpx.AsyncClient
) -> None:
    for route in owned_routes(users_app):
        if route.method == "get":
            response = await send(bob_client, route, world, world.upload_id)
            assert response.status_code != 404, f"{route}: {response.text[:200]}"


async def test_alice_cannot_retarget_bobs_mirror_nor_probe_with_it(
    world: World, users_container: Any, alice_client: httpx.AsyncClient
) -> None:
    engine = users_container.engine
    target = "https://www.youtube.com/playlist?list=PLalicewants"
    listings_before = len(engine.records.listings)
    response = await alice_client.patch(
        f"/api/mirrors/{world.mirror_id}", json={"source_url": target}
    )
    assert response.status_code == 404, response.text
    assert len(engine.records.listings) == listings_before, "no Engine call for a hidden Mirror"
    for owner in ("alice", "bob", "root"):
        assert users_container.sources.cached(token=None, url=target, owner=owner) is None


# --------------------------------------------------------------------------- member


MEMBER_READS: tuple[str, ...] = (
    "/api/feeds",
    "/api/mirrors",
    "/api/buckets",
    "/api/jobs",
    "/api/jobs?limit=500",
    "/api/jobs?feed_id={mirror_id}",
    "/api/storage",
    "/api/feeds.opml",
    "/api/keys",
    "/api/about",
    "/api/status",
)


async def test_alices_lists_never_carry_bobs_things(
    world: World, alice_client: httpx.AsyncClient
) -> None:
    for path in MEMBER_READS:
        response = await alice_client.get(path.replace("{mirror_id}", world.mirror_id))
        assert response.status_code == 200, f"{path}: {response.text[:200]}"
        assert not world.leaks(response.text), f"{path} leaks {world.leaks(response.text)}"


async def test_alices_crawlers_and_queue_count_only_her_own(
    world: World, alice_client: httpx.AsyncClient, root_client: httpx.AsyncClient
) -> None:
    def component(status: dict[str, Any], ident: str) -> dict[str, Any]:
        found: dict[str, Any] = next(c for c in status["components"] if c["id"] == ident)
        return found

    alices = (await alice_client.get("/api/status")).json()
    roots = (await root_client.get("/api/status")).json()
    assert world.refresh_error not in str(component(alices, "crawlers"))
    assert world.refresh_error in str(component(roots, "crawlers"))
    alice_about = (await alice_client.get("/api/about")).json()
    root_about = (await root_client.get("/api/about")).json()
    assert alice_about["totals"]["feeds"] == 1
    assert alice_about["totals"]["episodes"] == 0
    assert root_about["totals"]["feeds"] >= 5 and root_about["totals"]["episodes"] >= 1
    alice_storage = (await alice_client.get("/api/storage")).json()
    root_storage = (await root_client.get("/api/storage")).json()
    assert world.bucket_id not in str(alice_storage)
    # storage_summary covers the caller's own Feeds, for an Admin too (A27).
    assert world.bucket_id not in str(root_storage)


async def test_admins_list_every_feed_with_its_owner(
    world: World, root_client: httpx.AsyncClient
) -> None:
    feeds = {feed["id"]: feed for feed in (await root_client.get("/api/feeds")).json()["feeds"]}
    assert feeds[world.mirror_id]["owner"] == "bob"
    assert feeds[world.mirror_id]["owner_removed"] is False
    jobs = (await root_client.get("/api/jobs", params={"limit": 500})).json()["jobs"]
    assert world.job_id in {job["id"] for job in jobs}
    keys = (await root_client.get("/api/keys")).json()["keys"]
    assert world.key_id not in {key["id"] for key in keys}, "keys are by actor, Admins too"


# --------------------------------------------------------------------------- admin


async def test_admin_capabilities_refuse_members_with_403(
    world: World, users_app: Any, alice_client: httpx.AsyncClient
) -> None:
    del world
    routes = [route for route in api_routes(users_app) if access_of(route) == Access.admin]
    assert {route.capability for route in routes} == set(ADMIN_BODIES)
    for route in routes:
        assert route.capability is not None
        response = await alice_client.request(
            route.method.upper(), route.path, json=ADMIN_BODIES[route.capability]
        )
        assert response.status_code == 403, f"{route}: {response.text}"
        assert response.json()["type"].endswith("forbidden")


# --------------------------------------------------------------------------- engine_options


async def test_engine_options_are_an_admins_to_set_or_change(
    world: World, alice_client: httpx.AsyncClient, root_client: httpx.AsyncClient, origin: Any
) -> None:
    url = origin.url_for("rss/edge_cases.xml")
    tuned = {"source_url": url, "backfill": {"mode": "new"}, "engine_options": {"ratelimit": 5}}
    refused = await alice_client.post("/api/mirrors", json=tuned)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == "create_mirror (engine_options) needs an Admin"
    plain = await alice_client.post(
        "/api/mirrors", json={"source_url": url, "backfill": {"mode": "new"}}
    )
    assert plain.status_code == 201, plain.text
    path = f"/api/mirrors/{plain.json()['id']}"
    # Sending the value it already has changes nothing: a Member's settings form may.
    same = await alice_client.patch(path, json={"engine_options": {}, "title": "Mine"})
    assert same.status_code == 200, same.text
    changed = await alice_client.patch(path, json={"engine_options": {"ratelimit": 5}})
    assert changed.status_code == 403
    assert changed.json()["detail"] == "update_mirror (engine_options) needs an Admin"
    # An Admin may, on any Feed it sees (Bob's here) and on creation.
    by_admin = await root_client.patch(
        f"/api/mirrors/{world.mirror_id}", json={"engine_options": {"ratelimit": 5}}
    )
    assert by_admin.status_code == 200 and by_admin.json()["engine_options"] == {"ratelimit": 5}
    created = await root_client.post("/api/mirrors", json=tuned)
    assert created.status_code == 201 and created.json()["engine_options"] == {"ratelimit": 5}


# --------------------------------------------------------------------------- uploads


@pytest.mark.xfail(
    strict=True, reason="uploads ownership (get_upload NotFound) is group D's, spec §3.8"
)
async def test_alice_cannot_read_bobs_upload(world: World, alice_client: httpx.AsyncClient) -> None:
    response = await alice_client.get(f"/api/uploads/{world.upload_id}")
    assert response.status_code == 404


# --------------------------------------------------------------------------- removed users


async def test_a_removed_users_feeds_stay_for_admins_and_lock_him_out(
    world: World,
    users_settings: Any,
    engine: Any,
    root_client: httpx.AsyncClient,
    users_client: httpx.AsyncClient,
) -> None:
    """Bob leaves the configuration: root sees his Feeds as "owner removed", Alice still
    sees nothing, and his key and feed pair stop working."""
    feed = (await root_client.get(f"/api/feeds/{world.mirror_id}")).json()
    pair = feed["feed_credentials"]
    opened = await users_client.get(
        f"/feeds/{world.mirror_id}.xml", auth=(pair["username"], pair["password"])
    )
    assert opened.status_code == 200, "the pair works while Bob is configured"
    without_bob = settings_with(users_settings, auth=users_auth(users=("root", "alice")))
    async with running(without_bob, engine) as (_, http):
        await login(http, "root")
        feeds = {f["id"]: f for f in (await http.get("/api/feeds")).json()["feeds"]}
        assert feeds[world.mirror_id]["owner"] == "bob"
        assert feeds[world.mirror_id]["owner_removed"] is True
        assert feeds[world.bucket_id]["owner_removed"] is True
        http.cookies.clear()
        await login(http, "alice")
        response = await http.get(f"/api/feeds/{world.mirror_id}")
        assert response.status_code == 404
        http.cookies.clear()
        keyed = await http.get(
            "/api/feeds", headers={"Authorization": f"Bearer {world.key_secret}"}
        )
        assert keyed.status_code == 401
        refused = await http.get(
            f"/feeds/{world.mirror_id}.xml", auth=(pair["username"], pair["password"])
        )
        assert refused.status_code == 401
