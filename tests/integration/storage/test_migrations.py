"""Migrations: upgrade to head, downgrade to base, upgrade again, and no model drift."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    "instance_secrets",
    "users",
    "sessions",
    "login_attempts",
}
HEAD = "0017"


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
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password) "
                    "VALUES ('x', 'copycast', 'bogus', 'Bogus', 'u', 'p')"
                )
            )


async def test_kind_shape_constraint(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_feeds_kind_shape"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password) "
                    "VALUES ('m1', 'copycast', 'mirror', 'No source', 'u', 'p')"
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


async def test_0011_adds_a_nullable_checked_live_status(db_engine: AsyncEngine) -> None:
    """Existing rows start unknown (NULL); the column only takes yt-dlp's five values."""
    await downgrade_schema(db_engine, "0010")
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password, source_url, "
                "source_dedup_key, source_kind, backfill_mode) VALUES "
                "('tube-live', 'mirror', 'Tube', 'u', 'p', 'https://www.youtube.com/@x/videos', "
                "'youtube.com/@x', 'ytdlp', 'all')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO catalog_items (id, feed_id, source_key, ordinal, title, "
                "last_listed_at) VALUES ('7777777777777777', 'tube-live', 'Youtube:v7', 1, 'T', "
                "now())"
            )
        )
    await ensure_schema(db_engine)
    async with db_engine.connect() as conn:
        found = (
            await conn.execute(
                text("SELECT live_status FROM catalog_items WHERE id = '7777777777777777'")
            )
        ).scalar_one()
    assert found is None
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE catalog_items SET live_status = 'was_live' WHERE id = '7777777777777777'")
        )
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_catalog_items_live_status"):
            await conn.execute(
                text(
                    "UPDATE catalog_items SET live_status = 'streaming' "
                    "WHERE id = '7777777777777777'"
                )
            )
    assert await schema_is_current(db_engine)


async def test_0010_backfills_public_ext_from_the_media_file_else_the_source_kind(
    db_engine: AsyncEngine,
) -> None:
    """Archived rows keep their file's extension (the URL apps hold); the rest follow the
    Source: mp3 for RSS (the 1.2 placeholder), m4a for an Engine Source and for a Bucket.
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


async def test_0014_turns_inboxes_into_buckets(db_engine: AsyncEngine) -> None:
    """The kind changes, the id and title stay; a downgrade turns them back."""
    await downgrade_schema(db_engine, "0013")
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password) "
                "VALUES ('inbox-old', 'inbox', 'Old', 'u', 'p')"
            )
        )
    await ensure_schema(db_engine)
    select = text("SELECT kind, title FROM feeds WHERE id = 'inbox-old'")
    async with db_engine.connect() as conn:
        assert tuple((await conn.execute(select)).one()) == ("bucket", "Old")
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_feeds_kind"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password) "
                    "VALUES ('legacy', 'copycast', 'inbox', 'Legacy', 'u', 'p')"
                )
            )
    await downgrade_schema(db_engine, "0013")
    async with db_engine.connect() as conn:
        assert tuple((await conn.execute(select)).one()) == ("inbox", "Old")
    await ensure_schema(db_engine)


async def test_0015_locks_what_archive_mirrors_archived(db_engine: AsyncEngine) -> None:
    """Archived Episodes of Mirrors on the Archive policy without Sync are locked, and those
    Mirrors' intent is bumped so feed.json follows; nothing else is touched."""
    await downgrade_schema(db_engine, "0014")
    async with db_engine.begin() as conn:
        mirrors = (
            ("keep-all", "all", False),
            ("keep-new", "new", False),
            ("keep-sel", "selection", False),
            ("synced", "all", True),
            ("eph", "automatic", False),
        )
        for feed_id, mode, sync in mirrors:
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, kind, title, auth_username, auth_password, "
                    "source_url, source_dedup_key, source_kind, backfill_mode, sync_deletions) "
                    "VALUES (:id, 'mirror', 'M', 'u', 'p', :url, :url, 'rss', :mode, :sync)"
                ),
                {
                    "id": feed_id,
                    "url": f"https://p.example/{feed_id}.xml",
                    "mode": mode,
                    "sync": sync,
                },
            )
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password) "
                "VALUES ('local', 'bucket', 'Local', 'u', 'p')"
            )
        )
        rows = [
            ("1111111111111111", "keep-all", "archived"),
            ("2222222222222222", "keep-all", "available"),
            ("3333333333333333", "keep-new", "archived"),
            ("4444444444444444", "eph", "archived"),
            ("5555555555555555", "local", "archived"),
            ("6666666666666666", "keep-sel", "archived"),
            ("7777777777777777", "synced", "archived"),
        ]
        for ordinal, (item_id, feed_id, state) in enumerate(rows, start=1):
            await conn.execute(
                text(
                    "INSERT INTO catalog_items (id, feed_id, source_key, ordinal, title, "
                    "last_listed_at, archive_state) VALUES (:id, :feed_id, :key, :ordinal, "
                    "'T', now(), :state)"
                ),
                {
                    "id": item_id,
                    "feed_id": feed_id,
                    "key": f"urn:old:{item_id}",
                    "ordinal": ordinal,
                    "state": state,
                },
            )
    await ensure_schema(db_engine)
    async with db_engine.connect() as conn:
        found = dict((await conn.execute(text("SELECT id, archive_flag FROM catalog_items"))).all())
    assert found == {
        "1111111111111111": True,
        "2222222222222222": False,  # not archived
        "3333333333333333": True,
        "4444444444444444": False,  # Ephemeral
        "5555555555555555": False,  # a Local feed
        "6666666666666666": True,  # a selection is an Archive mode
        "7777777777777777": False,  # Sync: never locked automatically
    }
    async with db_engine.connect() as conn:
        intents = dict((await conn.execute(text("SELECT id, intent_version FROM feeds"))).all())
    bumped = {feed_id for feed_id, version in intents.items() if version > 1}
    assert bumped == {"keep-all", "keep-new", "keep-sel"}
    # The downgrade keeps the locks.
    await downgrade_schema(db_engine, "0014")
    async with db_engine.connect() as conn:
        kept = (
            await conn.execute(
                text("SELECT archive_flag FROM catalog_items WHERE id = '1111111111111111'")
            )
        ).scalar_one()
    assert kept is True
    await ensure_schema(db_engine)
    assert await schema_is_current(db_engine)


async def test_0016_creates_instance_secrets_and_its_downgrade_drops_it(
    db_engine: AsyncEngine,
) -> None:
    """The table starts empty (the api mints the secret on first start); the downgrade drops
    it, so the next upgrade starts over with a new secret."""
    async with db_engine.connect() as conn:
        assert "instance_secrets" in await conn.run_sync(_table_names)
        count = (await conn.execute(text("SELECT count(*) FROM instance_secrets"))).scalar_one()
    assert count == 0
    await downgrade_schema(db_engine, "0015")
    async with db_engine.connect() as conn:
        assert "instance_secrets" not in await conn.run_sync(_table_names)
    await ensure_schema(db_engine)
    assert await schema_is_current(db_engine)


# --------------------------------------------------------------------------- 0017


async def _seed_0016(db_engine: AsyncEngine) -> None:
    """At 0016: a Mirror, the default Bucket, an API key and a job, none with an owner."""
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password, source_url, "
                "source_dedup_key, source_kind, backfill_mode) VALUES ('m1', 'mirror', 'M', "
                "'u', 'p', 'https://p.example/f.xml', 'p.example/f.xml', 'rss', 'all')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO feeds (id, kind, title, auth_username, auth_password, is_default) "
                "VALUES ('copycast-abc234', 'bucket', 'Copycast', 'u', 'p', true)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO api_keys (id, name, scope, key_hash, prefix) VALUES "
                "(gen_random_uuid(), 'agent', 'read', repeat('a', 64), 'cck_aaaaaaaa')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO jobs (id, kind, trigger, feed_id) VALUES (gen_random_uuid(), "
                "'refresh', 'manual', 'm1')"
            )
        )


async def _owners(db_engine: AsyncEngine) -> tuple[set[str], set[str], set[str | None], set[str]]:
    async with db_engine.connect() as conn:
        feeds = set((await conn.execute(text("SELECT owner FROM feeds"))).scalars())
        keys = set((await conn.execute(text("SELECT owner FROM api_keys"))).scalars())
        jobs = set((await conn.execute(text("SELECT requested_by FROM jobs"))).scalars())
        users = set((await conn.execute(text("SELECT username FROM users"))).scalars())
    return feeds, keys, jobs, users


async def test_template_holds_the_open_mode_admin(db_engine: AsyncEngine) -> None:
    """0017 inserts the owner of existing data even without anything to own."""
    _, _, _, users = await _owners(db_engine)
    assert users == {"copycast"}


async def test_0017_backfills_owner(db_engine: AsyncEngine) -> None:
    """Existing Feeds and keys go to the owner handed over; jobs keep no requester; the
    Source and the default Local feed become unique per owner."""
    await downgrade_schema(db_engine, "0016")
    await _seed_0016(db_engine)
    await ensure_schema(db_engine, owner_of_existing="florent")
    assert await _owners(db_engine) == ({"florent"}, {"florent"}, {None}, {"florent"})
    async with db_engine.connect() as conn:
        blocked = (
            await conn.execute(text("SELECT blocked_reason, blocked_bytes FROM catalog_items"))
        ).all()
    assert blocked == []
    async with db_engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (username) VALUES ('emilia')"))
        # Another owner may mirror the same Source and have a default Local feed of their own.
        await conn.execute(
            text(
                "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password, "
                "source_url, source_dedup_key, source_kind, backfill_mode) VALUES ('m2', "
                "'emilia', 'mirror', 'M', 'u', 'p', 'https://p.example/f.xml', "
                "'p.example/f.xml', 'rss', 'all')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password, "
                "is_default) VALUES ('copycast-emi234', 'emilia', 'bucket', 'Copycast', 'u', "
                "'p', true)"
            )
        )
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="uq_feeds_owner_source_dedup_key"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password, "
                    "source_url, source_dedup_key, source_kind, backfill_mode) VALUES ('m3', "
                    "'florent', 'mirror', 'M', 'u', 'p', 'https://p.example/f.xml', "
                    "'p.example/f.xml', 'rss', 'all')"
                )
            )
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ix_feeds_owner_default"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password, "
                    "is_default) VALUES ('copycast-flo234', 'florent', 'bucket', 'C', 'u', "
                    "'p', true)"
                )
            )
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="fk_feeds_owner_users"):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password) "
                    "VALUES ('b9', 'nobody', 'bucket', 'B', 'u', 'p')"
                )
            )
    async with db_engine.begin() as conn:
        with pytest.raises(Exception, match="ck_catalog_items_blocked_reason"):
            await conn.execute(
                text(
                    "INSERT INTO catalog_items (id, feed_id, source_key, ordinal, title, "
                    "last_listed_at, blocked_reason) VALUES ('1111111111111111', 'm1', 'k', 1, "
                    "'T', now(), 'disk')"
                )
            )


async def test_0017_falls_back_to_the_settings(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that hands over no owner (a plain ``alembic upgrade``) gets the settings' one:
    the legacy login name, slugged."""
    await downgrade_schema(db_engine, "0016")
    await _seed_0016(db_engine)
    monkeypatch.setenv("COPYCAST__AUTH__USERNAME", "Me@Example.org")
    await ensure_schema(db_engine)
    feeds, keys, _, users = await _owners(db_engine)
    assert feeds == keys == users == {"me-example.org"}


async def test_0017_downgrade_refuses_shared_source(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (username) VALUES ('bob')"))
        for feed_id, owner in (("m1", "copycast"), ("m2", "bob")):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password, "
                    "source_url, source_dedup_key, source_kind, backfill_mode) VALUES (:id, "
                    ":owner, 'mirror', 'M', 'u', 'p', 'https://p.example/f.xml', "
                    "'p.example/f.xml', 'rss', 'all')"
                ),
                {"id": feed_id, "owner": owner},
            )
    with pytest.raises(
        RuntimeError, match=r"several owners mirror the same Source .*m1 \(copycast\), m2 \(bob\)"
    ):
        await downgrade_schema(db_engine, "0016")
    assert await current_revision(db_engine) == HEAD
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM feeds WHERE id = 'm2'"))
    await downgrade_schema(db_engine, "0016")
    async with db_engine.connect() as conn:
        columns = {c["name"] for c in await conn.run_sync(_columns, "feeds")}
        assert "owner" not in columns
        assert {"users", "sessions", "login_attempts"}.isdisjoint(await conn.run_sync(_table_names))
    await ensure_schema(db_engine, owner_of_existing="copycast")
    assert await schema_is_current(db_engine)


async def test_0017_downgrade_refuses_two_default_local_feeds(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(text("INSERT INTO users (username) VALUES ('bob')"))
        for feed_id, owner in (("copycast-aaa234", "copycast"), ("copycast-bbb234", "bob")):
            await conn.execute(
                text(
                    "INSERT INTO feeds (id, owner, kind, title, auth_username, auth_password, "
                    "is_default) VALUES (:id, :owner, 'bucket', 'Copycast', 'u', 'p', true)"
                ),
                {"id": feed_id, "owner": owner},
            )
    with pytest.raises(RuntimeError, match="several default Local feeds"):
        await downgrade_schema(db_engine, "0016")
    assert await current_revision(db_engine) == HEAD


def _columns(conn: Connection, table: str) -> list[dict[str, Any]]:
    return [dict(column) for column in inspect(conn).get_columns(table)]


def test_copycast_migrate_hands_over_the_settings_owner(
    db: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``copycast migrate`` passes ``settings.auth``'s owner explicitly (the legacy Admin)."""
    import asyncio

    from click.testing import CliRunner

    from copycast.adapters.db.session import create_db_engine
    from copycast.cli import main
    from copycast.settings import get_settings

    engine_settings = get_settings(database_url=db)

    async def _prepare() -> None:
        engine = create_db_engine(engine_settings)
        try:
            await downgrade_schema(engine, "0016")
            await _seed_0016(engine)
        finally:
            await engine.dispose()

    async def _read() -> tuple[set[str], set[str], set[str | None], set[str]]:
        engine = create_db_engine(engine_settings)
        try:
            return await _owners(engine)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    monkeypatch.setenv("COPYCAST__DATABASE_URL", db)
    monkeypatch.setenv("COPYCAST__STORAGE__URL", "memory://migrate-cli")
    monkeypatch.setenv("COPYCAST__STORAGE__SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("COPYCAST__AUTH__USERNAME", "Florent")
    monkeypatch.setenv("COPYCAST__AUTH__PASSWORD", "correct horse battery")
    result = CliRunner().invoke(main, ["migrate"])
    assert result.exit_code == 0, result.output
    feeds, keys, jobs, users = asyncio.run(_read())
    assert (feeds, keys, jobs, users) == ({"florent"}, {"florent"}, {None}, {"florent"})


FLORENT_USERS = [
    {
        "username": "florent",
        "password_hash": "$argon2id$v=19$m=1024,t=1,p=1$c2FsdHNhbHRzYWx0$"
        "U0HvE4ipdHl1ykcyEHWQ8ZbcFwm5S8okpQN0Dj7cmKI",
        "admin": True,
    },
    {
        "username": "emilia",
        "password_hash": "$argon2id$v=19$m=1024,t=1,p=1$c2FsdHNhbHRzYWx0$"
        "U0HvE4ipdHl1ykcyEHWQ8ZbcFwm5S8okpQN0Dj7cmKI",
    },
]


async def test_worker_first_migrates_with_owner_of_existing(
    db: str, tmp_path: Path, storage_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """In compose the worker may win MIGRATE_LOCK: it hands over the same owner as the api.

    Users mode, ``owner_of_existing = "florent"`` and no ``copycast`` user: whichever
    process migrates, every existing Feed and key goes to florent.
    """
    from copycast.adapters.db.locks import WORKER_LOCK, SessionLock
    from copycast.adapters.db.session import create_db_engine
    from copycast.logging import get_logger
    from copycast.settings import get_settings
    from copycast.worker import main as worker_module
    from copycast.worker.constants import EXIT_LOCKED
    from tests.support.fake_engine import FakeEngine

    # A logger of its own: once logging is configured (a CLI test ran first), the module's
    # logger would be cached here and escape the worker suite's capture_logs later on.
    monkeypatch.setattr(worker_module, "log", get_logger("tests.worker_first"))

    settings = get_settings(
        database_url=db,
        storage={**storage_config, "scratch_dir": tmp_path / "scratch"},
        auth={"users": FLORENT_USERS, "owner_of_existing": "florent"},
    )
    engine = create_db_engine(settings)
    try:
        await downgrade_schema(engine, "0016")
        await _seed_0016(engine)
        # Another worker holds WORKER_LOCK: this one migrates, then stops right there.
        async with SessionLock(engine, WORKER_LOCK) as lock:
            assert lock.held
            assert await worker_module.main(settings, engine=FakeEngine()) == EXIT_LOCKED
        feeds, keys, jobs, users = await _owners(engine)
    finally:
        await engine.dispose()
    assert (feeds, keys, jobs) == ({"florent"}, {"florent"}, {None})
    assert users == {"florent"}  # no "copycast" user anywhere


def test_copycast_migrate_in_users_mode_hands_over_owner_of_existing(
    db: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio
    import json

    from click.testing import CliRunner

    from copycast.adapters.db.session import create_db_engine
    from copycast.cli import main
    from copycast.settings import get_settings

    engine_settings = get_settings(database_url=db)

    async def _prepare() -> None:
        engine = create_db_engine(engine_settings)
        try:
            await downgrade_schema(engine, "0016")
            await _seed_0016(engine)
        finally:
            await engine.dispose()

    async def _read() -> tuple[set[str], set[str], set[str | None], set[str]]:
        engine = create_db_engine(engine_settings)
        try:
            return await _owners(engine)
        finally:
            await engine.dispose()

    asyncio.run(_prepare())
    monkeypatch.setenv("COPYCAST__DATABASE_URL", db)
    monkeypatch.setenv("COPYCAST__STORAGE__URL", "memory://migrate-cli-users")
    monkeypatch.setenv("COPYCAST__STORAGE__SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.setenv("COPYCAST__AUTH__USERS", json.dumps(FLORENT_USERS[::-1]))
    monkeypatch.setenv("COPYCAST__AUTH__OWNER_OF_EXISTING", "florent")
    result = CliRunner().invoke(main, ["migrate"])
    assert result.exit_code == 0, result.output
    assert asyncio.run(_read()) == ({"florent"}, {"florent"}, {None}, {"florent"})
