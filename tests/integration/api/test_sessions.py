"""Users mode: sign-in, sessions, throttling, TOTP, API keys on ``/api`` and removed Users.

The app runs with ``root`` (Admin), ``alice`` and ``bob`` (Members), whose hashes use
the cheapest argon2 parameters (``tests.support.users``).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import structlog
from sqlalchemy import text

from copycast.app import Container, build_container
from copycast.application.passwords import PasswordHasher
from copycast.application.services import auth as auth_service
from copycast.domain.credentials import session_digest
from copycast.domain.enums import PrincipalVia
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


# The app's clock against Postgres's: the app (on the host) writes lock and session times
# with its own clock, while Postgres's now() runs in the container, whose clock drifts a
# few ms from the host's. Tests that check those times read and write them on the app's
# clock only; they run with it skewed both ways to prove they never compare it with now().
CLOCK_SKEWS = pytest.mark.parametrize("skew", [0.0, 1.5, -1.5], ids=["same", "ahead", "behind"])


def skew_app_clock(monkeypatch: pytest.MonkeyPatch, skew: float) -> None:
    monkeypatch.setattr(auth_service, "_now", lambda: datetime.now(UTC) + timedelta(seconds=skew))


def app_now() -> datetime:
    return auth_service._now()  # pyright: ignore[reportPrivateUsage]


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


@CLOCK_SKEWS
async def test_five_failures_lock_the_name_known_or_not(
    users_client: httpx.AsyncClient,
    users_container: Container,
    monkeypatch: pytest.MonkeyPatch,
    skew: float,
) -> None:
    skew_app_clock(monkeypatch, skew)
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

    # The lock over, one more failure locks for twice as long (times on the app's clock).
    await execute(
        users_container,
        "UPDATE login_attempts SET locked_until = :t WHERE username = 'alice'",
        t=app_now() - timedelta(seconds=1),
    )
    before = app_now()
    assert (await attempt(users_client, "alice", "wrong-password")).status_code == 401
    after = app_now()
    locked_until = await scalar(
        users_container, "SELECT locked_until FROM login_attempts WHERE username = 'alice'"
    )
    assert before + timedelta(seconds=60) <= locked_until <= after + timedelta(seconds=60)
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


async def test_a_burst_of_guesses_verifies_no_more_than_the_lock_allows(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    """Regression: the lock check, the verify and the count were not atomic.

    Eighteen concurrent wrong passwords (every one the process lets wait for a hash
    slot) used to all be verified and answered 401. Now each attempt is counted before
    its verify, so only the threshold's worth runs; the rest see the lock at once.
    """
    burst = auth_service.HASH_SLOTS + auth_service.HASH_QUEUE_MAX
    responses = await asyncio.gather(
        *(attempt(users_client, "alice", f"guess-{n:02d}") for n in range(burst))
    )
    codes = sorted(response.status_code for response in responses)
    assert codes == [401] * auth_service.LOCK_THRESHOLD + [429] * (
        burst - auth_service.LOCK_THRESHOLD
    )
    count = await scalar(
        users_container, "SELECT failed_count FROM login_attempts WHERE username = 'alice'"
    )
    assert count == auth_service.LOCK_THRESHOLD
    # The right password, sent after the burst, does not get through the lock.
    assert (await attempt(users_client, "alice", USERS["alice"].password)).status_code == 429


async def test_a_burst_with_the_right_password_in_it_is_still_capped(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    """Four failures recorded: of a burst of guesses, one at most is verified."""
    for _ in range(auth_service.LOCK_THRESHOLD - 1):
        assert (await attempt(users_client, "alice", "wrong-password")).status_code == 401
    guesses = [f"guess-{n:02d}" for n in range(9)] + [USERS["alice"].password]
    responses = await asyncio.gather(*(attempt(users_client, "alice", g) for g in guesses))
    verified = [r.status_code for r in responses if r.status_code != 429]
    assert len(verified) == 1


async def test_an_unknown_typed_name_is_never_stored_in_clear(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    typed = "Hunter2-Typed-In-The-Wrong-Field"
    for _ in range(auth_service.LOCK_THRESHOLD):
        assert (await attempt(users_client, typed, "x")).status_code == 401
    # It still locks like any name, under a key that reveals nothing.
    assert (await attempt(users_client, typed, "x")).status_code == 429
    keys = await scalar(users_container, "SELECT array_agg(username) FROM login_attempts")
    (key,) = keys
    assert key.startswith(auth_service.UNKNOWN_NAME_PREFIX) and len(key) == 33
    assert typed.lower() not in key and "hunter2" not in key
    # Another unknown name gets a key of its own.
    assert (await attempt(users_client, "someone-else", "x")).status_code == 401
    assert await scalar(users_container, "SELECT count(*) FROM login_attempts") == 2


async def test_a_login_body_over_the_cap_is_refused_unread(
    users_client: httpx.AsyncClient, users_container: Container
) -> None:
    """Regression: POST /auth/login read and parsed a body of any size (64 MiB: 422)."""
    huge = b'{"username": "alice", "password": "' + b"x" * (64 * 1024 * 1024) + b'"}'
    response = await users_client.post("/auth/login", content=huge)
    assert response.status_code == 413
    assert problem(response)["type"] == "urn:copycast:problem:payload-too-large"
    too_long_code = await attempt(users_client, "alice", USERS["alice"].password, "1" * 33)
    assert too_long_code.status_code == 422
    # Neither counted a failure, and a sign-in of ordinary size still works.
    assert await scalar(users_container, "SELECT count(*) FROM login_attempts") == 0
    assert (await attempt(users_client, "alice", USERS["alice"].password)).status_code == 204


async def test_logins_prune_stale_attempts_now_and_then(
    users_client: httpx.AsyncClient, users_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def seed_stale() -> None:
        await execute(
            users_container,
            "INSERT INTO login_attempts (username, failed_count, updated_at) VALUES "
            "('?stale', 1, now() - interval '25 hours')",
        )

    stale = "SELECT count(*) FROM login_attempts WHERE username = '?stale'"
    # Pruned recently: a login leaves the stale row alone.
    await seed_stale()
    monkeypatch.setattr(auth_service._Prune, "last", time.monotonic())  # pyright: ignore[reportPrivateUsage]
    assert (await attempt(users_client, "bob", "wrong-password")).status_code == 401
    assert await scalar(users_container, stale) == 1
    # Due: the next login prunes it, without waiting for a restart.
    monkeypatch.setattr(auth_service._Prune, "last", None)  # pyright: ignore[reportPrivateUsage]
    assert (await attempt(users_client, "bob", "wrong-password")).status_code == 401
    assert await scalar(users_container, stale) == 0


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


async def test_a_non_ascii_digit_code_is_the_same_401_with_the_right_password(
    settings: Settings, db: str
) -> None:
    configured = settings_with(settings, auth=users_auth(totp={"root": TOTP_SECRET}))
    async with running(configured) as (_, client):
        arabic = "\u0661\u0662\u0663\u0664\u0665\u0666"
        wrong = await attempt(client, "root", "wrong-password", arabic)
        right = await attempt(client, "root", USERS["root"].password, arabic)
        assert wrong.status_code == right.status_code == 401
        assert problem(wrong) == problem(right)


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


@CLOCK_SKEWS
async def test_sessions_slide_and_expire(
    users_client: httpx.AsyncClient,
    users_container: Container,
    monkeypatch: pytest.MonkeyPatch,
    skew: float,
) -> None:
    skew_app_clock(monkeypatch, skew)
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
    # Seen more than a minute ago: the expiry slides to session_days from now (the app's).
    await execute(
        users_container,
        "UPDATE sessions SET last_seen_at = now() - interval '2 minutes' WHERE id_hash = :d",
        d=digest,
    )
    before = app_now()
    assert (await users_client.get(api("/feeds"))).status_code == 200
    after = app_now()
    expires_at = await scalar(
        users_container, "SELECT expires_at FROM sessions WHERE id_hash = :d", d=digest
    )
    assert before + timedelta(days=30) <= expires_at <= after + timedelta(days=30)
    # Expired: refused, and the row is gone.
    await execute(
        users_container,
        "UPDATE sessions SET expires_at = :t WHERE id_hash = :d",
        t=app_now() - timedelta(seconds=1),
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


# --------------------------------------------------------------------------- legacy mode

LEGACY_PASSWORD = "longenoughpw"


def legacy(settings: Settings, password: str = LEGACY_PASSWORD, **auth: Any) -> Settings:
    return settings_with(settings, auth={"username": "florent", "password": password, **auth})


async def test_legacy_mode_keeps_only_an_argon2_hash_of_the_operator_password(
    settings: Settings, db: str
) -> None:
    """Regression: an unsalted SHA-256 of the operator password was stored in Postgres."""
    async with running(legacy(settings)) as (container, client):
        stored = await scalar(
            container, "SELECT credential_digest FROM users WHERE username = 'florent'"
        )
        assert bytes(stored).startswith(b"$argon2id$")
        assert PasswordHasher().verify(bytes(stored).decode(), LEGACY_PASSWORD)
        # Startup hashed the operator password off the event loop, once.
        assert "_legacy_hash" in vars(container.users)
        session = await session_headers(client, "florent", LEGACY_PASSWORD)
    # A restart with the same password keeps the session and the stored hash.
    async with running(legacy(settings)) as (container, client):
        assert (await client.get(api("/me"), headers=session)).status_code == 200
        again = await scalar(
            container, "SELECT credential_digest FROM users WHERE username = 'florent'"
        )
        assert bytes(again) == bytes(stored)
    # A new password signs it out.
    async with running(legacy(settings, "a-new-password")) as (_, client):
        assert (await client.get(api("/me"), headers=session)).status_code == 401


async def test_get_me_never_hashes_the_legacy_password_on_the_event_loop(
    settings: Settings, db: str
) -> None:
    """Regression: GET /api/me after a restart, before any login, ran argon2 in the loop."""
    container = build_container(legacy(settings), engine=FakeEngine())
    try:
        principal = container.users.principal("florent", via=PrincipalVia.session)
        assert principal is not None
        me = await container.services_for(principal).get_me()
        assert me.username == "florent" and me.totp is False
        assert "_legacy_hash" not in vars(container.users)
    finally:
        await container.aclose()


async def test_a_renamed_legacy_operator_is_warned_about(settings: Settings, db: str) -> None:
    """Regression: Feeds of the previous legacy owner lost every credential silently."""
    async with running(settings) as (_, client):  # open mode: the Owner is "copycast"
        created = await client.post(api("/buckets"), json={"name": "Later"})
        assert created.status_code == 201 and created.json()["owner"] == "copycast"
    with structlog.testing.capture_logs() as events:
        async with running(legacy(settings)):
            pass
    warnings = [e for e in events if e["event"] == "auth.removed_owner_has_feeds"]
    assert len(warnings) == 1
    assert warnings[0]["owner"] == "copycast" and warnings[0]["feeds"] == 2
    assert warnings[0]["log_level"] == "warning"
    assert "set auth.username back to copycast" in warnings[0]["hint"]
    assert "copycast users reassign copycast florent" in warnings[0]["hint"]
    # Back to the old name: copycast's Feeds are fine, florent's default is the orphan now.
    with structlog.testing.capture_logs() as events:
        async with running(legacy(settings, username="copycast")):
            pass
    warnings = [e for e in events if e["event"] == "auth.removed_owner_has_feeds"]
    assert [(e["owner"], e["feeds"]) for e in warnings] == [("florent", 1)]


async def test_legacy_operator_pairs_on_feeds_are_throttled_like_a_sign_in(
    settings: Settings, db: str
) -> None:
    """Regression: the operator password could be guessed on /feeds at line speed.

    A pair naming ``auth.username`` counts on the legacy owner's ``login_attempts`` row,
    the one the login form counts on, before its password is compared.
    """
    right = httpx.BasicAuth("florent", LEGACY_PASSWORD)
    wrong = httpx.BasicAuth("florent", "not-the-password")
    async with running(legacy(settings)) as (container, client):
        # The right pair passes the dependency: the feed does not exist (404).
        assert (await client.get("/feeds/nope.xml", auth=right)).status_code == 404
        for n in range(1, auth_service.LOCK_THRESHOLD + 1):
            assert (await client.get("/feeds/nope.xml", auth=wrong)).status_code == 401, n
        # Locked: the right pair gets the same 401, and so does the login form (429).
        locked = await client.get("/feeds/nope.xml", auth=right)
        assert locked.status_code == 401
        assert locked.headers["www-authenticate"].startswith("Basic")
        assert (await attempt(client, "florent", LEGACY_PASSWORD)).status_code == 429
        count = await scalar(
            container, "SELECT failed_count FROM login_attempts WHERE username = 'florent'"
        )
        assert count == auth_service.LOCK_THRESHOLD
        # Another name is no operator attempt: compared as a feed pair, never counted.
        other = httpx.BasicAuth("someone", LEGACY_PASSWORD)
        assert (await client.get("/feeds/nope.xml", auth=other)).status_code == 401
        # The lock over, the right pair opens again and resets the count.
        await execute(
            container,
            "UPDATE login_attempts SET locked_until = :t",
            t=app_now() - timedelta(seconds=1),
        )
        assert (await client.get("/feeds/nope.xml", auth=right)).status_code == 404
        assert await scalar(container, "SELECT count(*) FROM login_attempts") == 0
