"""problem+json everywhere an API request fails."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from copycast.application.services import about as about_module
from tests.support.paths import ABOUT_PATH, api

pytestmark = pytest.mark.integration

PROBLEM = "application/problem+json"


async def test_unknown_feed_is_a_problem(client: httpx.AsyncClient) -> None:
    response = await client.get(api("/feeds/none-such"))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM)
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["type"] == "urn:copycast:problem:not-found"
    assert body["title"] == "Not found" and body["status"] == 404
    assert body["instance"] == api("/feeds/none-such")
    assert "none-such" in body["detail"]


async def test_unknown_route_and_method(client: httpx.AsyncClient) -> None:
    missing = await client.get(api("/nothing/here"))
    assert missing.status_code == 404
    assert missing.json()["type"] == "urn:copycast:problem:not-found"
    wrong = await client.put(ABOUT_PATH)
    assert wrong.status_code == 405
    assert wrong.json()["type"] == "urn:copycast:problem:method-not-allowed"


async def test_validation_problem_lists_errors(client: httpx.AsyncClient) -> None:
    response = await client.get(api("/feeds/x/items"), params={"limit": 5000})
    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "urn:copycast:problem:validation"
    assert body["errors"][0]["loc"] == ["query", "limit"]
    bad_uuid = await client.get(api("/jobs/not-a-uuid"))
    assert bad_uuid.status_code == 422
    bad_json = await client.post(
        api("/probe"), content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert bad_json.status_code == 422


async def test_unhandled_error_is_an_internal_problem(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(ctx: object) -> object:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(about_module, "about", boom)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get(ABOUT_PATH)
    assert response.status_code == 500
    assert response.headers["content-type"].startswith(PROBLEM)
    body = response.json()
    assert body["type"] == "urn:copycast:problem:internal"
    assert "kaboom" not in body.get("detail", "")
