"""The web UI mount: only with COPYCAST_WEB_DIR; immutable assets; a catch-all sparing the API."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from copycast.adapters.api.app import create_app
from copycast.app import build_container
from copycast.settings import Settings, get_settings
from tests.support.fake_engine import FakeEngine


@pytest.fixture
def web_dir(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Copycast</title>", encoding="utf-8")
    (root / "assets" / "app-abc123.js").write_text("console.log('hi')", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return root


@pytest.fixture
def spa_settings(settings: Settings, web_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("COPYCAST_WEB_DIR", str(web_dir))
    return get_settings(
        base_url=settings.base_url, data_dir=settings.data_dir, database_url=settings.database_url
    )


async def spa_client(settings: Settings) -> httpx.AsyncClient:
    app = create_app(settings, build_container(settings, engine=FakeEngine()))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_spa_is_served_only_when_web_dir_is_set(settings: Settings) -> None:
    async with await spa_client(settings) as client:
        response = await client.get("/mirrors")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")


async def test_spa_routes(spa_settings: Settings) -> None:
    async with await spa_client(spa_settings) as client:
        for path in ("/", "/mirrors", "/mirrors/abc?tab=catalog", "/inboxes/x/y/z"):
            page = await client.get(path)
            assert page.status_code == 200, path
            assert page.headers["content-type"].startswith("text/html")
            assert page.headers["cache-control"] == "no-cache"
            assert b"<title>Copycast</title>" in page.content

        asset = await client.get("/assets/app-abc123.js")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert (await client.get("/assets/missing.js")).status_code == 404

        favicon = await client.get("/favicon.svg")
        assert favicon.status_code == 200 and favicon.headers["cache-control"] == "no-cache"

        for reserved in ("/api/nothing", "/feeds/x/y", "/healthz/nope", "/mcp/extra/deep"):
            response = await client.get(reserved)
            assert response.status_code in (404, 405), reserved
            assert not response.headers["content-type"].startswith("text/html"), reserved

        head = await client.head("/mirrors")
        assert head.status_code == 200 and head.content == b""
