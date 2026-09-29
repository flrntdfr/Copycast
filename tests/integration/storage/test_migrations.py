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
    "api_keys",
}
HEAD = "0010"


def _table_names(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names())


async def test_template_is_at_head(db_engine: AsyncEngine) -> None:
    assert head_revision() == HEAD
    assert await current_revision(db_engine) == HEAD
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
    assert diff == [], f"models and migrations differ: {diff}"


async def test_enum_check_constraints_are_named_and_enforced(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_feeds_kind"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, kind, title, auth_username, auth_password) "
                    "VALUES ('x', 'bogus', 'Bogus', 'u', 'p')"
                )
            )


async def test_kind_shape_constraint(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_feeds_kind_shape"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, kind, title, auth_username, auth_password) "
                    "VALUES ('m1', 'mirror', 'No source', 'u', 'p')"
                )
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


async def test_0002_mints_a_pair_for_every_existing_feed(db_engine: AsyncEngine) -> None:
    await downgrade_schema(db_engine, "0001")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO feeds (id, kind, title) VALUES ('inbox-old', 'inbox', 'Old')")
        )
    await ensure_schema(db_engine)
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT auth_username, auth_password FROM feeds WHERE id = 'inbox-old'")
            )
        ).one()
    assert len(row[0]) == 8 and len(row[1]) == 24
    assert await schema_is_current(db_engine)


async def test_0010_backfills_public_ext_from_the_media_file_else_the_source_kind(
    db_engine: AsyncEngine,
) -> None:
    """Archived rows keep their file's extension (the URL apps hold); the rest follow the
    Source: mp3 for RSS (the 1.2 placeholder), m4a for an Engine Source and for an Inbox.
    A media_path the extractor's pattern does not match (a dot only in a directory) falls
    through to the Source rule instead of aborting the upgrade with a NULL."""
    await downgrade_schema(db_engine, "0009")
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password, source_url, "
                "source_dedup_key, source_kind, backfill_mode) VALUES "
                "('rss-old', 'mirror', 'RSS', 'u', 'p', 'https://p.example/feed.xml', "
                "'p.example/feed.xml', 'rss', 'all'), "
                "('tube-old', 'mirror', 'Tube', 'u', 'p', 'https://www.youtube.com/@x/videos', "
                "'youtube.com/@x', 'ytdlp', 'all')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password) "
                "VALUES ('inbox-old', 'inbox', 'Inbox', 'u', 'p')"
            )
        )
        rows = [
            ("1111111111111111", "rss-old", 1, "media/1111111111111111.M4A"),
            ("2222222222222222", "rss-old", 2, "media/2222222222222222.mp3"),
            ("3333333333333333", "rss-old", 3, None),
            ("4444444444444444", "rss-old", 4, "media.d/4444444444444444"),
            ("5555555555555555", "tube-old", 1, None),
            ("6666666666666666", "inbox-old", 1, None),
        ]
        for item_id, feed_id, ordinal, media_path in rows:
            await conn.execute(
                text(
                    "INSERT INTO catalog_items (id, feed_id, source_key, ordinal, title, "
                    "last_listed_at, media_path) VALUES (:id, :feed_id, :key, :ordinal, 'T', "
                    "now(), :media_path)"
                ),
                {
                    "id": item_id,
                    "feed_id": feed_id,
                    "key": f"urn:old:{item_id}",
                    "ordinal": ordinal,
                    "media_path": media_path,
                },
            )
    await ensure_schema(db_engine)
    async with db_engine.connect() as conn:
        found = dict((await conn.execute(text("SELECT id, public_ext FROM catalog_items"))).all())
    assert found == {
        "1111111111111111": "m4a",  # archived: the file's extension, lower-cased
        "2222222222222222": "mp3",
        "3333333333333333": "mp3",  # unarchived RSS: the placeholder 1.2 advertised
        "4444444444444444": "mp3",  # no file extension: the Source rule, not NULL
        "5555555555555555": "m4a",  # an Engine Source archives to m4a
        "6666666666666666": "m4a",  # an Inbox is filled by the Engine (predicted_ext(None))
    }
    assert await schema_is_current(db_engine)
