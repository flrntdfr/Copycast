"""Migrations: upgrade to head, downgrade to base, upgrade again, and no model drift."""

from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from copycast.adapters.db.migrate import (
    SchemaNotCurrent,
    await_schema,
    current_revision,
    downgrade_schema,
    ensure_schema,
    head_revision,
    prepare_schema,
    schema_is_current,
)
from copycast.adapters.db.models import Base

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "alembic_version",
    "feeds",
    "catalog_items",
    "assets",
    "requests",
    "request_items",
    "jobs",
    "job_log_lines",
    "refresh_runs",
    "worker_heartbeat",
    "engine_versions",
}


def _table_names(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


async def test_template_is_at_head(db_engine: AsyncEngine) -> None:
    assert head_revision() == "0001"
    assert await current_revision(db_engine) == "0001"
    assert await schema_is_current(db_engine)
    async with db_engine.connect() as conn:
        assert await conn.run_sync(_table_names) == EXPECTED_TABLES


async def test_ensure_schema_is_idempotent(db_engine: AsyncEngine) -> None:
    await ensure_schema(db_engine)
    await ensure_schema(db_engine)
    assert await schema_is_current(db_engine)


async def test_downgrade_then_upgrade_round_trip(db_engine: AsyncEngine) -> None:
    await downgrade_schema(db_engine, "base")
    assert await current_revision(db_engine) is None
    assert not await schema_is_current(db_engine)
    async with db_engine.connect() as conn:
        assert await conn.run_sync(_table_names) == {"alembic_version"}
    await ensure_schema(db_engine)
    assert await schema_is_current(db_engine)
    async with db_engine.connect() as conn:
        assert await conn.run_sync(_table_names) == EXPECTED_TABLES


async def test_no_drift_between_models_and_migrations(db_engine: AsyncEngine) -> None:
    def _diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": False}
        )
        return compare_metadata(context, Base.metadata)

    async with db_engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == [], f"models and migration 0001 differ: {diff}"


async def test_enum_check_constraints_are_named_and_enforced(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_feeds_kind"):
            await conn.execute(
                text("INSERT INTO feeds (id, kind, title) VALUES ('x', 'bogus', 'Bogus')")
            )


async def test_kind_shape_constraint(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_feeds_kind_shape"):
            await conn.execute(
                text("INSERT INTO feeds (id, kind, title) VALUES ('m1', 'mirror', 'No source')")
            )


async def test_await_schema_returns_when_current(db_engine: AsyncEngine) -> None:
    await await_schema(db_engine, wait_seconds=1.0, poll=0.1)
    await prepare_schema(db_engine, auto_migrate=False, wait_seconds=1.0)


async def test_await_schema_times_out_when_behind(db_engine: AsyncEngine) -> None:
    await downgrade_schema(db_engine, "base")
    with pytest.raises(SchemaNotCurrent, match="copycast migrate"):
        await await_schema(db_engine, wait_seconds=0.3, poll=0.1)
    await prepare_schema(db_engine, auto_migrate=True)
    assert await schema_is_current(db_engine)
