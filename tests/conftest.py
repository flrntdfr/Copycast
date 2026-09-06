"""Shared fixtures.

Unit tests use only ``settings``, ``data_dir``, ``engine`` and ``origin``.
Postgres-backed fixtures (``pg_url``, ``db``, ``container``, ``client``,
``mcp_client``) import the adapters lazily so the unit suite runs before those
packages exist, and touch Docker only when a test asks for them.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest

from copycast.settings import Settings, get_settings
from tests.support.fake_engine import FakeEngine
from tests.support.origin import FIXTURES_DIR, Origin

TEMPLATE_DB = "copycast_template"
LAYOUT_VERSION = "1\n"
TEST_BASE_URL = "http://testserver"


def _load(module: str, attr: str) -> Any:
    return getattr(importlib.import_module(module), attr)


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, ""))


def _psycopg_url(url: str) -> str:
    """SQLAlchemy ``postgresql+psycopg://`` -> plain ``postgresql://`` for psycopg."""
    parts = urlsplit(url)
    scheme = "postgresql" if parts.scheme.startswith("postgres") else parts.scheme
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, ""))


def _admin_exec(admin_url: str, *statements: str) -> None:
    import psycopg

    with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never read the developer's config file or COPYCAST__ environment during tests."""
    monkeypatch.setenv("COPYCAST_CONFIG", str(tmp_path / "no-such-config.toml"))
    for name in list(os.environ):
        if name.startswith("COPYCAST__"):
            monkeypatch.delenv(name)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A data directory already carrying LAYOUT_VERSION and an empty feeds/."""
    root = tmp_path / "data"
    (root / "feeds").mkdir(parents=True)
    (root / "LAYOUT_VERSION").write_text(LAYOUT_VERSION, encoding="utf-8")
    return root


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def origin() -> Iterator[Origin]:
    with Origin() as server:
        yield server


# --------------------------------------------------------------------------- postgres


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """A superuser URL: ``COPYCAST_TEST_DATABASE_URL`` or a testcontainers ``postgres:17``."""
    env_url = os.environ.get("COPYCAST_TEST_DATABASE_URL")
    if env_url:
        yield env_url
        return
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # pragma: no cover - dev group always has it
        pytest.skip("testcontainers not installed and COPYCAST_TEST_DATABASE_URL unset")
    try:
        container = PostgresContainer("postgres:17", username="copycast", password="copycast")
        container.start()
    except Exception as exc:  # docker missing or daemon down
        pytest.skip(f"no Postgres available for integration tests: {exc}")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        yield f"postgresql://copycast:copycast@{host}:{port}/{container.dbname}"
    finally:
        container.stop()


@pytest.fixture(scope="session")
def template_db_url(pg_url: str) -> str:
    """``copycast_template``, migrated to head once per session."""
    _admin_exec(
        pg_url,
        f"DROP DATABASE IF EXISTS {TEMPLATE_DB} WITH (FORCE)",
        f"CREATE DATABASE {TEMPLATE_DB}",
    )
    template_url = _with_database(pg_url, TEMPLATE_DB)
    ensure_schema = _load("copycast.adapters.db.migrate", "ensure_schema")
    settings = get_settings(database_url=template_url, base_url=TEST_BASE_URL)
    create_db_engine = _load("copycast.adapters.db.session", "create_db_engine")

    async def _migrate() -> None:
        db_engine = create_db_engine(settings)
        try:
            await ensure_schema(db_engine)
        finally:
            await db_engine.dispose()

    asyncio.run(_migrate())
    return template_url


@pytest.fixture
def db(pg_url: str, template_db_url: str) -> Iterator[str]:
    """A fresh database cloned from the template; dropped afterwards."""
    name = f"copycast_test_{uuid.uuid4().hex[:12]}"
    _admin_exec(pg_url, f"CREATE DATABASE {name} TEMPLATE {TEMPLATE_DB}")
    try:
        yield _with_database(template_db_url, name)
    finally:
        _admin_exec(pg_url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


# --------------------------------------------------------------------------- application


@pytest.fixture
def settings(data_dir: Path, request: pytest.FixtureRequest) -> Settings:
    """Settings over ``data_dir``; uses the per-test ``db`` only when the test asked for it."""
    database_url = "postgresql+psycopg://copycast:copycast@localhost:5432/copycast_unused"
    if "db" in request.fixturenames:
        database_url = request.getfixturevalue("db")
    return get_settings(
        base_url=TEST_BASE_URL,
        data_dir=data_dir,
        database_url=database_url,
        refresh={"interval_hours": 24, "fetch_cooldown_minutes": 15, "concurrency": 1},
    )


@pytest.fixture
async def container(settings: Settings, engine: FakeEngine, db: str) -> AsyncIterator[Any]:
    """``copycast.app.build_container`` over the FakeEngine and the per-test database."""
    build_container = _load("copycast.app", "build_container")
    built = build_container(settings, engine=engine)
    try:
        yield built
    finally:
        await built.aclose()


@pytest.fixture
async def app(settings: Settings, container: Any) -> AsyncIterator[Any]:
    """The FastAPI app with its lifespan running (ASGITransport does not run it)."""
    create_app = _load("copycast.adapters.api.app", "create_app")
    application = create_app(settings, container)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[Any]:
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as http:
        yield http


@pytest.fixture
async def mcp_client(settings: Settings, container: Any, app: Any) -> AsyncIterator[Any]:
    """An in-memory fastmcp client over ``adapters.mcp.server.create_mcp``."""
    from fastmcp import Client

    create_mcp = _load("copycast.adapters.mcp.server", "create_mcp")
    server = create_mcp(settings, container)
    async with Client(server) as mcp:
        yield mcp
