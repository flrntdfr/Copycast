"""Users mode: sign-in, sessions, throttling, TOTP, API keys on ``/api`` and removed Users.

The app runs with ``root`` (Admin), ``alice`` and ``bob`` (Members), whose hashes use
the cheapest argon2 parameters (``tests.support.users``).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import structlog
from sqlalchemy import text

from copycast.app import Container
from copycast.application.passwords import PasswordHasher
from copycast.application.services import auth as auth_service
from copycast.domain.credentials import session_digest
from copycast.domain.totp import decode_secret, hotp, step_at
from copycast.settings import Settings
from tests.integration.api.conftest import running, settings_with
from tests.support.audio import TINY_MP3
from tests.support.fake_engine import FakeEngine
from tests.support.paths import MCP_PATH, api
from tests.support.users import (
    TOTP_SECRET,
    USERS,
    login,
    mint_key,
    password_hash,
    session_headers,
    user_entry,
    users_auth,
)

pytestmark = pytest.mark.integration


def problem(response: httpx.Response) -> dict[str, Any]:
    assert response.headers["content-type"].startswith("application/problem+json"), response.text
    body: dict[str, Any] = response.json()
    return body


def cookie_value(response: httpx.Response) -> str:
    """The session token a ``Set-Cookie`` carries."""
    return response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


async def attempt(
    client: httpx.AsyncClient, username: str, password: str, totp: str | None = None
) -> httpx.Response:
    body: dict[str, Any] = {"username": username, "password": password}
    if totp is not None:
        body["totp"] = totp
    response = await client.post("/auth/login", json=body)
    client.cookies.clear()
    return response


async def scalar(container: Container, sql: str, **params: Any) -> Any:
    async with container.uow_factory() as uow:
        result = await uow.session.execute(text(sql), params)  # type: ignore[attr-defined]
        return result.scalar()


async def execute(container: Container, sql: str, **params: Any) -> None:
    async with container.uow_factory() as uow:
        await uow.session.execute(text(sql), params)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- the boundary


async def test_api_without_a_session_is_401_without_a_challenge(
    users_client: httpx.AsyncClient,
) -> None:
    for path in (api("/feeds"), api("/me"), api("/openapi.json"), api("/docs")):
        response = await users_client.get(path)
        assert response.status_code == 401, path
        assert "www-authenticate" not in response.headers, path
        assert response.headers["cache-control"] == "no-store"
        assert problem(response)["type"] == "urn:copycast:problem:unauthorized"
    async with users_client.stream("GET", api("/events")) as events:
        assert events.status_code == 401
    # Signing out needs no session; the health checks need nothing.
    assert (await users_client.post("/auth/logout")).status_code == 204
    assert (await users_client.get("/healthz/live")).status_code == 200


async def test_sign_in_sets_the_cookie_and_me_answers(users_client: httpx.AsyncClient) -> None:
    # The name is stripped and lower-cased.
    response = await login(users_client, "ALICE ", USERS["alice"].password)
    header = response.headers["set-cookie"]
    assert header.startswith("copycast_session=")
    assert "HttpOnly" in header and "Path=/" in header and "SameSite=lax" in header
    assert "Max-Age=259" in header and "Secure" not in header  # http: no __Host-, no Secure
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b""  # the token travels in the cookie only

    me = await users_client.get(api("/me"))
    assert me.status_code == 200
    assert me.json() == {
        "username": "alice",
        "admin": False,
        "auth_mode": "users",
        "via": "session",
        "totp": False,
        "quota": {
            "username": "alice",
            "used_bytes": 0,
            "quota_bytes": None,
            "state": "ok",
            "removed": False,
        },
    }
    # /api/me re-issues the cookie so the browser's copy slides too.
    assert me.headers["set-cookie"].startswith(f"copycast_session={cookie_value(response)};")
    # Every configured User got their own default Local feed at startup.
    feeds = (await users_client.get(api("/feeds?kind=bucket"))).json()["feeds"]
    assert any(feed["is_default"] for feed in feeds)


async def test_open_mode_has_no_sign_in(client: httpx.AsyncClient) -> None:
    refused = await client.post("/auth/login", json={"username": "copycast", "password": "x"})
    assert refused.status_code == 404
    assert (await client.post("/auth/logout")).status_code == 204
    me = (await client.get(api("/me"))).json()
    assert me["username"] == "copycast" and me["admin"] is True
    assert me["auth_mode"] == "open" and me["via"] == "open"


# --------------------------------------------------------------------------- failures


async def test_unknown_user_and_wrong_password_answer_alike_after_one_verify(
    users_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_verify, real_dummy = PasswordHasher.verify, PasswordHasher.dummy_verify

    def verify(self: PasswordHasher, phc: str, password: str) -> bool:
        calls.append("verify")
        return real_verify(self, phc, password)

    def dummy_verify(self: PasswordHasher, password: str) -> None:
        calls.append("dummy")
        real_dummy(self, password)

    monkeypatch.setattr(PasswordHasher, "verify", verify)
    monkeypatch.setattr(PasswordHasher, "dummy_verify", dummy_verify)

    unknown = await attempt(users_client, "mallory", "whatever-password")
    assert calls == ["dummy"]
    calls.clear()
    wrong = await attempt(users_client, "alice", "not-her-password")
    assert calls == ["verify"]
    calls.clear()
    assert unknown.status_code == wrong.status_code == 401
    assert "www-authenticate" not in unknown.headers and "set-cookie" not in wrong.headers
    left, right = problem(unknown), problem(wrong)
    left.pop("instance"), right.pop("instance")
    assert left == right
    assert left["type"] == "urn:copycast:problem:login-failed"
    assert left["detail"] == "wrong username, password or code"
    # The right password runs one verify too, and nothing else.
    await login(users_client, "alice")
    assert calls == ["verify"]


@pytest.mark.parametrize(("failures", "seconds"), [(4, 0), (5, 30), (6, 60), (7, 120), (9, 480)])
def test_the_lock_schedule(failures: int, seconds: int) -> None:
    assert auth_service.lock_seconds(failures) == seconds
    assert auth_service.lock_seconds(40) == auth_service.LOCK_MAX_SECONDS == 900


async def test_five_failures_lock_the_name_known_or_not(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    for name in ("alice", "nobody"):
        for n in range(1, 6):
            response = await attempt(users_client, name, "wrong-password")
            assert response.status_code == 401, (name, n)
        locked = await attempt(users_client, name, "wrong-password")
        assert locked.status_code == 429, name
        assert problem(locked)["type"] == "urn:copycast:problem:login-locked"
        assert 25 <= int(locked.headers["retry-after"]) <= 30
        assert problem(locked)["retry_after"] == int(locked.headers["retry-after"])
    # The right password does not get through a lock.
    assert (await attempt(users_client, "alice", USERS["alice"].password)).status_code == 429
    assert (await attempt(users_client, "bob", USERS["bob"].password)).status_code == 204

    # The lock over, one more failure locks for twice as long.
    await execute(
        users_container,
        "UPDATE login_attempts SET locked_until = now() - interval '1 second' "
        "WHERE username = 'alice'",
    )
    assert (await attempt(users_client, "alice", "wrong-password")).status_code == 401
    seconds = await scalar(
        users_container,
        "SELECT extract(epoch FROM locked_until - now()) FROM login_attempts "
        "WHERE username = 'alice'",
    )
    assert 55 <= float(seconds) <= 60
    # A success after the lock resets the count.
    await execute(users_container, "UPDATE login_attempts SET locked_until = NULL")
    assert (await attempt(users_client, "alice", USERS["alice"].password)).status_code == 204
    gone = await scalar(
        users_container, "SELECT count(*) FROM login_attempts WHERE username = 'alice'"
    )
    assert gone == 0


async def test_a_full_hash_queue_answers_busy_without_counting_a_failure(
    users_client: httpx.AsyncClient, users_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = auth_service.HASH_SLOTS + auth_service.HASH_QUEUE_MAX
    monkeypatch.setattr(auth_service._HashQueue, "pending", full)  # pyright: ignore[reportPrivateUsage]
    busy = await attempt(users_client, "alice", "wrong-password")
    assert busy.status_code == 503
    assert busy.headers["retry-after"] == "5"
    assert problem(busy)["type"] == "urn:copycast:problem:login-busy"
    assert await scalar(users_container, "SELECT count(*) FROM login_attempts") == 0


# --------------------------------------------------------------------------- TOTP and https


async def test_totp_is_required_and_good_once(settings: Settings, db: str) -> None:
    configured = settings_with(settings, auth=users_auth(totp={"root": TOTP_SECRET}))
    async with running(configured) as (_, client):
        password = USERS["root"].password
        key = decode_secret(TOTP_SECRET)
        now = step_at(time.time())
        window = {hotp(key, step) for step in range(now - 1, now + 3)}
        wrong = next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in window)
        assert (await attempt(client, "root", password)).status_code == 401
        assert (await attempt(client, "root", password, wrong)).status_code == 401
        code = hotp(key, step_at(time.time()))
        signed = await attempt(client, "root", password, code[:3] + " " + code[3:])
        assert signed.status_code == 204
        replayed = await attempt(client, "root", password, code)
        assert replayed.status_code == 401
        assert problem(replayed)["type"] == "urn:copycast:problem:login-failed"
        # A code sent for a User without TOTP is ignored.
        assert (
            await attempt(client, "alice", USERS["alice"].password, "123456")
        ).status_code == 204
        me = await client.get(
            api("/me"), headers={"Cookie": f"copycast_session={cookie_value(signed)}"}
        )
        assert me.json()["totp"] is True and me.json()["admin"] is True


async def test_an_https_site(settings: Settings, db: str) -> None:
    configured = settings_with(settings, base_url="https://copycast.example", auth=users_auth())
    async with running(configured, base_url="https://copycast.example") as (container, client):
        # An Admin without TOTP cannot sign in on https (logged), a Member can.
        with structlog.testing.capture_logs() as events:
            refused = await attempt(client, "root", USERS["root"].password)
        assert refused.status_code == 401
        assert any(e.get("reason") == "admin_without_totp" for e in events)
        signed = await client.post(
            "/auth/login", json={"username": "alice", "password": USERS["alice"].password}
        )
        assert signed.status_code == 204
        header = signed.headers["set-cookie"]
        assert header.startswith("__Host-copycast_session=")
        assert "Secure" in header and "Path=/" in header and "HttpOnly" in header
        assert (await client.get(api("/me"))).json()["username"] == "alice"
        # The plain http cookie name means nothing here.
        token = cookie_value(signed)
        client.cookies.clear()
        plain = await client.get(api("/me"), headers={"Cookie": f"copycast_session={token}"})
        assert plain.status_code == 401
        assert container.users.https
    async with running(configured, base_url="http://copycast.example") as (_, plain_client):
        over_http = await attempt(plain_client, "alice", USERS["alice"].password)
        assert over_http.status_code == 400
        assert problem(over_http)["type"] == "urn:copycast:problem:https-required"
        assert problem(over_http)["detail"] == "sign in over https: https://copycast.example"


# --------------------------------------------------------------------------- sessions


async def test_logout_ends_the_session(users_client: httpx.AsyncClient) -> None:
    session = await session_headers(users_client, "bob")
    assert (await users_client.get(api("/me"), headers=session)).status_code == 200
    users_client.cookies.clear()
    out = await users_client.post("/auth/logout", headers=session)
    assert out.status_code == 204
    assert "copycast_session=" in out.headers["set-cookie"]
    assert "Max-Age=0" in out.headers["set-cookie"]
    assert (await users_client.get(api("/me"), headers=session)).status_code == 401
    assert (await users_client.post("/auth/logout", headers=session)).status_code == 204


async def test_a_login_ends_the_session_it_came_with(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    first = await session_headers(users_client, "alice")
    second = await users_client.post(
        "/auth/login",
        json={"username": "alice", "password": USERS["alice"].password},
        headers=first,
    )
    assert second.status_code == 204
    users_client.cookies.clear()
    assert (await users_client.get(api("/me"), headers=first)).status_code == 401
    fresh = {"Cookie": f"copycast_session={cookie_value(second)}"}
    assert (await users_client.get(api("/me"), headers=fresh)).status_code == 200
    assert await scalar(users_container, "SELECT count(*) FROM sessions") == 1


async def test_sessions_slide_and_expire(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    response = await login(users_client, "alice")
    digest = session_digest(cookie_value(response))
    stored = await scalar(
        users_container, "SELECT user_agent FROM sessions WHERE id_hash = :d", d=digest
    )
    assert stored == "python-httpx/" + httpx.__version__
    # Touched within the minute: nothing is written.
    await execute(
        users_container,
        "UPDATE sessions SET expires_at = now() + interval '1 day' WHERE id_hash = :d",
        d=digest,
    )
    assert (await users_client.get(api("/feeds"))).status_code == 200
    days = await scalar(
        users_container,
        "SELECT extract(epoch FROM expires_at - now()) / 86400 FROM sessions WHERE id_hash = :d",
        d=digest,
    )
    assert float(days) < 1.01
    # Seen more than a minute ago: the expiry slides to session_days from now.
    await execute(
        users_container,
        "UPDATE sessions SET last_seen_at = now() - interval '2 minutes' WHERE id_hash = :d",
        d=digest,
    )
    assert (await users_client.get(api("/feeds"))).status_code == 200
    days = await scalar(
        users_container,
        "SELECT extract(epoch FROM expires_at - now()) / 86400 FROM sessions WHERE id_hash = :d",
        d=digest,
    )
    assert 29.9 < float(days) <= 30
    # Expired: refused, and the row is gone.
    await execute(
        users_container,
        "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE id_hash = :d",
        d=digest,
    )
    assert (await users_client.get(api("/feeds"))).status_code == 401
    assert await scalar(users_container, "SELECT count(*) FROM sessions") == 0


async def test_a_changed_password_signs_out_that_user_only(
    users_settings: Settings, users_client: httpx.AsyncClient, users_container: Container
) -> None:
    alice = await session_headers(users_client, "alice")
    bob = await session_headers(users_client, "bob")
    await execute(
        users_container,
        "INSERT INTO login_attempts (username, failed_count, updated_at) VALUES "
        "('stale', 1, now() - interval '25 hours'), ('fresh', 1, now())",
    )
    # An unchanged configuration keeps every session.
    await users_container.services.sync_users()
    assert (await users_client.get(api("/me"), headers=bob)).status_code == 200
    users_client.cookies.clear()
    attempts = await scalar(
        users_container, "SELECT string_agg(username, ',' ORDER BY username) FROM login_attempts"
    )
    assert attempts == "fresh"

    new_hash = password_hash("bob-new-password")
    entries = [
        user_entry(USERS["root"]),
        user_entry(USERS["alice"]),
        user_entry(USERS["bob"], password_hash=new_hash),
    ]
    changed = settings_with(users_settings, auth={"users": entries})
    async with running(changed) as (_, client):
        assert (await client.get(api("/me"), headers=bob)).status_code == 401
        assert (await client.get(api("/me"), headers=alice)).status_code == 200
        assert (await attempt(client, "bob", "bob-new-password")).status_code == 204


async def test_a_removed_user_loses_sessions_keys_and_feed_pairs(
    users_settings: Settings, users_client: httpx.AsyncClient, users_container: Container
) -> None:
    session = await session_headers(users_client, "alice")
    users_client.headers.update(session)
    key = await mint_key(users_client, "read")
    bucket = (await users_client.post(api("/buckets"), json={"name": "Alice's"})).json()
    users_client.headers.pop("Cookie")
    users_client.cookies.clear()
    assert bucket["owner"] == "alice"
    pair = bucket["feed_credentials"]
    feed_path = f"/feeds/{bucket['id']}.xml"
    basic = httpx.BasicAuth(pair["username"], pair["password"])
    assert (await users_client.get(feed_path, auth=basic)).status_code == 200
    bearer = {"Authorization": f"Bearer {key}"}
    assert (await users_client.get(api("/feeds"), headers=bearer)).status_code == 200
    mcp_call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    mcp_headers = {**bearer, "Accept": "application/json, text/event-stream"}
    mcp = await users_client.post(MCP_PATH, json=mcp_call, headers=mcp_headers)
    assert mcp.status_code != 401

    without_alice = settings_with(users_settings, auth=users_auth(users=("root", "bob")))
    with structlog.testing.capture_logs() as events:
        async with running(without_alice) as (container, client):
            assert await scalar(container, "SELECT count(*) FROM sessions") == 0
            assert (await client.get(api("/me"), headers=session)).status_code == 401
            assert (await client.get(api("/feeds"), headers=bearer)).status_code == 401
            mcp = await client.post(MCP_PATH, json=mcp_call, headers=mcp_headers)
            assert mcp.status_code == 401  # the MCP mount refuses the key too
            refused = await client.get(feed_path, auth=basic)
            assert refused.status_code == 401
            assert refused.headers["www-authenticate"].startswith("Basic")
            # The data stays: the users row, the Feed and the key.
            assert await scalar(container, "SELECT count(*) FROM users WHERE username='alice'")
            assert await scalar(container, "SELECT count(*) FROM api_keys WHERE owner='alice'")
    warnings = [e for e in events if e["event"] == "auth.removed_owner_has_feeds"]
    assert warnings and warnings[0]["owner"] == "alice" and warnings[0]["feeds"] == 2
    assert "copycast users reassign alice <new>" in warnings[0]["hint"]

    # Adding the User back restores everything but the session.
    async with running(users_settings) as (_, client):
        assert (await client.get(feed_path, auth=basic)).status_code == 200
        assert (await client.get(api("/feeds"), headers=bearer)).status_code == 200


# --------------------------------------------------------------------------- keys and feeds


async def test_api_keys_on_api_reach_tool_routes_with_a_fitting_scope(
    users_client: httpx.AsyncClient,
) -> None:
    users_client.headers.update(await session_headers(users_client, "alice"))
    keys = {
        scope: await mint_key(users_client, scope, scope) for scope in ("read", "write", "full")
    }
    users_client.headers.pop("Cookie")
    users_client.cookies.clear()

    def bearer(scope: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {keys[scope]}"}

    assert (await users_client.get(api("/feeds"), headers=bearer("read"))).status_code == 200
    refused = await users_client.post(api("/buckets"), json={"name": "R"}, headers=bearer("read"))
    assert refused.status_code == 403
    assert problem(refused)["detail"] == (
        "create_bucket needs an API key with scope 'write'; this key has scope 'read'"
    )
    made = await users_client.post(api("/buckets"), json={"name": "W"}, headers=bearer("write"))
    assert made.status_code == 201 and made.json()["owner"] == "alice"
    feed_id = made.json()["id"]
    assert (
        await users_client.delete(api(f"/feeds/{feed_id}"), headers=bearer("write"))
    ).status_code == 403
    assert (
        await users_client.delete(api(f"/feeds/{feed_id}"), headers=bearer("full"))
    ).status_code == 204
    # Session data, key management and capability-less routes refuse every key.
    for method, path in (
        ("GET", api("/me")),
        ("GET", api("/keys")),
        ("GET", api("/mirrors")),
        ("GET", api("/settings/defaults")),
        ("GET", api("/engine/cookies")),
    ):
        response = await users_client.request(method, path, headers=bearer("full"))
        assert response.status_code == 403, path
        assert problem(response)["type"] == "urn:copycast:problem:forbidden"


async def test_feed_xml_opens_to_its_owner_and_admins_but_no_operator_pair(
    users_client: httpx.AsyncClient,
) -> None:
    alice = await session_headers(users_client, "alice")
    bucket = await users_client.post(api("/buckets"), json={"name": "Mine"}, headers=alice)
    feed_id = bucket.json()["id"]
    users_client.cookies.clear()
    feed_path = f"/feeds/{feed_id}.xml"
    assert (await users_client.get(feed_path, headers=alice)).status_code == 200
    assert (
        await users_client.get(feed_path, headers=await session_headers(users_client, "root"))
    ).status_code == 200
    bob = await users_client.get(feed_path, headers=await session_headers(users_client, "bob"))
    assert bob.status_code == 401
    # Media and assets still need the pair or the key, whoever the session is.
    media = await users_client.get(f"/feeds/{feed_id}/media/0123456789abcdef.mp3", headers=alice)
    assert media.status_code == 401
    # Users mode has no operator pair: a User's password opens no feed.
    root_pair = httpx.BasicAuth("root", USERS["root"].password)
    assert (await users_client.get(feed_path, auth=root_pair)).status_code == 401


async def test_an_upload_url_needs_no_cookie(users_client: httpx.AsyncClient) -> None:
    alice = await session_headers(users_client, "alice")
    staged = await users_client.post(api("/uploads"), json={"filename": "a.mp3"}, headers=alice)
    assert staged.status_code == 201, staged.text
    path = urlsplit(staged.json()["upload_url"]).path
    put = await users_client.put(path, content=TINY_MP3.read_bytes())
    assert put.status_code == 200 and put.json()["state"] == "received"


async def test_the_session_survives_the_startup_of_another_process(
    users_settings: Settings, users_client: httpx.AsyncClient
) -> None:
    """The worker runs ``sync_users`` too: an unchanged configuration signs nobody out."""
    session = await session_headers(users_client, "alice")
    async with running(users_settings, FakeEngine()) as (_, client):
        assert (await client.get(api("/me"), headers=session)).status_code == 200


async def test_the_cross_site_guard_covers_sign_in_and_cookie_requests(
    users_client: httpx.AsyncClient,
) -> None:
    """A session cookie rides along on cross-site requests: the 2.3.0 guard refuses them."""
    cross_site = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    body = {"username": "alice", "password": USERS["alice"].password}
    refused = await users_client.post("/auth/login", json=body, headers=cross_site)
    assert refused.status_code == 403
    assert problem(refused)["type"] == "urn:copycast:problem:cross-site-request"
    session = await session_headers(users_client, "alice")
    for method, path in (("POST", "/auth/logout"), ("POST", api("/buckets"))):
        response = await users_client.request(
            method, path, json={"name": "x"}, headers={**session, **cross_site}
        )
        assert response.status_code == 403, path
    # The page itself (same origin) gets through with the cookie.
    same_origin = {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"}
    made = await users_client.post(
        api("/buckets"), json={"name": "Mine"}, headers={**session, **same_origin}
    )
    assert made.status_code == 201, made.text
    assert (await users_client.get(api("/me"), headers=session)).status_code == 200
