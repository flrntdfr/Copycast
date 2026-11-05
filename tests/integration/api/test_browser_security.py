"""The cross-site guard and the security headers in the real app, auth off and on."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from copycast.adapters.api.app import create_app
from copycast.adapters.api.security import (
    CROSS_SITE_SLUG,
    PODCAST_POLICY,
    UNKNOWN_HOST_SLUG,
    spa_policy,
)
from copycast.app import build_container
from copycast.settings import Settings, get_settings
from tests.conftest import TEST_BASE_URL
from tests.support.fake_engine import FakeEngine
from tests.support.paths import MCP_PATH, api

OPERATOR = ("florent", "correct-horse-battery")
CROSS_SITE = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def app_client(settings: Settings, base_url: str = TEST_BASE_URL) -> httpx.AsyncClient:
    """The app without its lifespan: enough for requests refused before any route runs."""
    app = create_app(settings, build_container(settings, engine=FakeEngine()))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


def with_auth(settings: Settings, **overrides: Any) -> Settings:
    return get_settings(
        base_url=overrides.get("base_url", settings.base_url),
        storage=settings.storage.model_dump(),
        database_url=settings.database_url,
        auth={"username": OPERATOR[0], "password": OPERATOR[1]},
    )


def assert_refused(response: httpx.Response, slug: str = CROSS_SITE_SLUG) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["type"] == f"urn:copycast:problem:{slug}"
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("auth_on", [False, True])
async def test_cross_site_writes_are_refused_before_auth_and_routes(
    settings: Settings, auth_on: bool
) -> None:
    configured = with_auth(settings) if auth_on else settings
    async with app_client(configured) as client:
        for credentials in ({}, basic(*OPERATOR), basic("wrong", "pair")):
            headers = {**CROSS_SITE, **credentials}
            assert_refused(await client.post(api("/buckets"), json={"name": "x"}, headers=headers))
            assert_refused(await client.delete(api("/feeds/abc"), headers=headers))
            assert_refused(await client.put("/uploads/abc/AbCdEfGhIjKlMnOpQrSt", headers=headers))
            assert_refused(await client.post(MCP_PATH, json={}, headers=headers))
            assert_refused(await client.post(f"{MCP_PATH}/", json={}, headers=headers))


@pytest.mark.parametrize("auth_on", [False, True])
async def test_a_rebound_host_name_is_refused_on_every_path(
    settings: Settings, auth_on: bool
) -> None:
    """DNS rebinding: the attacker's page, now resolving to Copycast, is same-origin with
    itself. /mcp runs with the SDK's own host check off, so this one has to hold."""
    configured = with_auth(settings) if auth_on else settings
    rebound = {"Host": "evil.example:8080", "Sec-Fetch-Site": "same-origin"}
    with_origin = {"Host": "evil.example:8080", "Origin": "http://evil.example:8080"}
    # Over plain http a browser sends no Fetch Metadata, and a same-origin GET no Origin.
    plain_http = {"Host": "evil.example:8080"}
    async with app_client(configured) as client:
        for headers in (rebound, with_origin, plain_http, {**rebound, **basic(*OPERATOR)}):
            for response in (
                await client.get(api("/feeds"), headers=headers),
                await client.post(api("/buckets"), json={"name": "x"}, headers=headers),
                await client.post(MCP_PATH, json={}, headers=headers),
                await client.post(f"{MCP_PATH}/", json={}, headers=headers),
                await client.get("/feeds/abc.xml", headers=headers),
            ):
                assert_refused(response, UNKNOWN_HOST_SLUG)


async def test_an_allowed_host_name_passes_the_host_check(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPYCAST_ALLOWED_HOSTS", "copycast")
    configured = with_auth(settings)
    async with app_client(configured) as client:
        response = await client.get(
            api("/feeds"), headers={"Host": "copycast:8080", "Sec-Fetch-Site": "none"}
        )
    assert response.status_code == 401  # past the guard, at authentication


async def test_probes_are_not_host_checked(settings: Settings) -> None:
    async with app_client(with_auth(settings), base_url="http://10-42-0-17.pod:8080") as client:
        assert (await client.get("/healthz/live")).status_code == 200
        assert_refused(await client.get(api("/about")), UNKNOWN_HOST_SLUG)


async def test_without_fetch_metadata_a_foreign_origin_is_refused(settings: Settings) -> None:
    async with app_client(with_auth(settings)) as client:
        headers = {"Origin": "null", **basic(*OPERATOR)}
        assert_refused(await client.post(api("/buckets"), json={"name": "x"}, headers=headers))


async def test_auth_still_answers_same_origin_requests(settings: Settings) -> None:
    async with app_client(with_auth(settings)) as client:
        response = await client.post(
            api("/buckets"), json={"name": "x"}, headers={"Sec-Fetch-Site": "same-origin"}
        )
        assert response.status_code == 401
        assert response.headers["content-security-policy"] == spa_policy(TEST_BASE_URL)


@pytest.mark.integration
async def test_same_origin_and_non_browser_writes_reach_the_routes(
    client: httpx.AsyncClient,
) -> None:
    same_origin = {"Sec-Fetch-Site": "same-origin", "Origin": TEST_BASE_URL}
    created = await client.post(api("/buckets"), json={"name": "Browser"}, headers=same_origin)
    assert created.status_code == 201, created.text
    vite = {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-origin"}
    proxied = await client.post(api("/buckets"), json={"name": "Vite"}, headers=vite)
    assert proxied.status_code == 201, proxied.text
    curl = await client.post(api("/buckets"), json={"name": "Curl"})
    assert curl.status_code == 201, curl.text
    refused = await client.post(api("/buckets"), json={"name": "Evil"}, headers=CROSS_SITE)
    assert_refused(refused)
    listed = (await client.get(api("/feeds?kind=bucket"))).json()["feeds"]
    names = {feed["title"] for feed in listed}
    assert {"Browser", "Vite", "Curl"} <= names and "Evil" not in names


async def test_headers_on_the_api_feeds_and_robots(settings: Settings) -> None:
    configured = with_auth(settings, base_url="https://copycast.example")
    async with app_client(configured, base_url="https://copycast.example") as client:
        api_response = await client.get(api("/openapi.json"), headers=basic(*OPERATOR))
        assert api_response.status_code == 200
        assert api_response.headers["content-security-policy"] == spa_policy(
            "https://copycast.example"
        )
        assert api_response.headers["strict-transport-security"] == "max-age=31536000"
        assert api_response.headers["x-frame-options"] == "DENY"
        assert api_response.headers["x-robots-tag"]  # the existing layers still run

        feed = await client.get("/feeds/abc.xml")
        assert feed.status_code == 401
        assert feed.headers["x-content-type-options"] == "nosniff"
        assert feed.headers["referrer-policy"] == "no-referrer"
        assert feed.headers["content-security-policy"] == PODCAST_POLICY
        assert "strict-transport-security" not in feed.headers
        assert "x-frame-options" not in feed.headers

        logo = await client.get("/feeds/copycast-artwork.png")
        assert logo.status_code == 200
        assert logo.headers["content-security-policy"] == PODCAST_POLICY

        docs = await client.get(api("/docs"), headers=basic(*OPERATOR))
        assert docs.status_code == 200
        assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]


async def test_the_spa_shell_and_assets_get_the_policy(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Copycast</title>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("console.log('hi')", encoding="utf-8")
    monkeypatch.setenv("COPYCAST_WEB_DIR", str(root))
    spa_settings = get_settings(
        base_url=settings.base_url,
        storage=settings.storage.model_dump(),
        database_url=settings.database_url,
    )
    async with app_client(spa_settings) as client:
        for path in ("/", "/mirrors/abc", "/assets/app-abc123.js"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-security-policy"] == spa_policy(TEST_BASE_URL)
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cross-origin-opener-policy"] == "same-origin"
            assert "strict-transport-security" not in response.headers
        # /art carries no page policy: the shell must never be served under it.
        for path in ("/art", "/art/x", "/art/TOKEN.jpg"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert not response.headers["content-type"].startswith("text/html"), path
