"""Users and owners: every Feed, API key and job names the User it belongs to.

3.0 takes its Users from the configuration. This migration creates ``users`` (the
configured names, synced at startup, with no secret), ``sessions`` (browser logins, by
the SHA-256 of their token) and ``login_attempts`` (the per-username throttle, no foreign
key since unknown names are throttled too). It gives every existing Feed and API key to
the owner of existing data, which every migrating process passes explicitly
(``config.attributes["owner_of_existing"]``; a plain ``alembic upgrade`` falls back to
``settings.auth.effective_owner_of_existing``), and inserts that User even when there
is nothing to own, so a fresh database holds it. The Source dedup key and the default
Local feed become unique per owner. Jobs gain ``requested_by`` (existing rows stay
null), and Catalog items gain ``blocked_reason`` and ``blocked_bytes`` for the items an
Over quota Owner keeps waiting. Neither ``intent_version`` nor ``revision`` moves: the
Feed cache, the ETags and ``feed.json`` stay as they are.

The downgrade refuses when two owners share a Source or more than one default Local
feed exists, since the global constraints it restores would fail; otherwise it drops
everything this upgrade added, owners included.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-06 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BLOCKED_REASONS = "blocked_reason IN ('quota')"

INSERT_OWNER = "INSERT INTO users (username) VALUES (:owner) ON CONFLICT DO NOTHING"
BACKFILL_FEEDS = "UPDATE feeds SET owner = :owner"
BACKFILL_KEYS = "UPDATE api_keys SET owner = :owner"

SHARED_SOURCES = """
SELECT source_dedup_key, string_agg(id || ' (' || owner || ')', ', ' ORDER BY id)
FROM feeds
WHERE source_dedup_key IS NOT NULL
GROUP BY source_dedup_key
HAVING count(DISTINCT owner) > 1
ORDER BY source_dedup_key
"""
DEFAULT_FEEDS = "SELECT id, owner FROM feeds WHERE is_default ORDER BY owner, id"


def owner_of_existing() -> str:
    """The owner the migrating process handed over, else the settings' (never a constant)."""
    owner = context.config.attributes.get("owner_of_existing")
    if owner is None:
        from copycast.settings import get_settings

        owner = get_settings().auth.effective_owner_of_existing
    return str(owner)


def upgrade() -> None:
    owner = owner_of_existing()
    op.create_table(
        "users",
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("credential_digest", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("username", name=op.f("pk_users")),
    )
    op.execute(sa.text(INSERT_OWNER).bindparams(owner=owner))

    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.LargeBinary(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["username"],
            ["users.username"],
            name=op.f("fk_sessions_username_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_username"), "sessions", ["username"])
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"])

    op.create_table(
        "login_attempts",
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("username", name=op.f("pk_login_attempts")),
    )

    op.add_column("feeds", sa.Column("owner", sa.Text(), nullable=True))
    op.execute(sa.text(BACKFILL_FEEDS).bindparams(owner=owner))
    op.alter_column("feeds", "owner", nullable=False)
    op.create_foreign_key(
        op.f("fk_feeds_owner_users"),
        "feeds",
        "users",
        ["owner"],
        ["username"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("uq_feeds_source_dedup_key"), "feeds", type_="unique")
    op.create_unique_constraint(
        op.f("uq_feeds_owner_source_dedup_key"), "feeds", ["owner", "source_dedup_key"]
    )
    op.drop_index(op.f("ix_feeds_is_default"), table_name="feeds")
    op.create_index(
        op.f("ix_feeds_owner_default"),
        "feeds",
        ["owner"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.add_column("api_keys", sa.Column("owner", sa.Text(), nullable=True))
    op.execute(sa.text(BACKFILL_KEYS).bindparams(owner=owner))
    op.alter_column("api_keys", "owner", nullable=False)
    op.create_foreign_key(
        op.f("fk_api_keys_owner_users"),
        "api_keys",
        "users",
        ["owner"],
        ["username"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_api_keys_owner"), "api_keys", ["owner"])

    op.add_column("jobs", sa.Column("requested_by", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_jobs_requested_by_users"),
        "jobs",
        "users",
        ["requested_by"],
        ["username"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_jobs_requested_by"), "jobs", ["requested_by"])

    op.add_column("catalog_items", sa.Column("blocked_reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_catalog_items_blocked_reason"), "catalog_items", BLOCKED_REASONS
    )
    op.add_column("catalog_items", sa.Column("blocked_bytes", sa.BigInteger(), nullable=True))
    op.create_index(
        op.f("ix_catalog_items_blocked"),
        "catalog_items",
        ["feed_id"],
        postgresql_where=sa.text("blocked_reason IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    shared = bind.execute(sa.text(SHARED_SOURCES)).all()
    if shared:
        detail = "; ".join(f"{key}: {feeds}" for key, feeds in shared)
        raise RuntimeError(
            f"cannot downgrade below 0017: several owners mirror the same Source ({detail})"
        )
    defaults = bind.execute(sa.text(DEFAULT_FEEDS)).all()
    if len(defaults) > 1:
        detail = ", ".join(f"{feed_id} ({owner})" for feed_id, owner in defaults)
        raise RuntimeError(
            f"cannot downgrade below 0017: several default Local feeds exist ({detail})"
        )

    op.drop_index(op.f("ix_catalog_items_blocked"), table_name="catalog_items")
    op.drop_column("catalog_items", "blocked_bytes")
    op.drop_constraint(op.f("ck_catalog_items_blocked_reason"), "catalog_items", type_="check")
    op.drop_column("catalog_items", "blocked_reason")

    op.drop_index(op.f("ix_jobs_requested_by"), table_name="jobs")
    op.drop_constraint(op.f("fk_jobs_requested_by_users"), "jobs", type_="foreignkey")
    op.drop_column("jobs", "requested_by")

    op.drop_index(op.f("ix_api_keys_owner"), table_name="api_keys")
    op.drop_constraint(op.f("fk_api_keys_owner_users"), "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "owner")

    op.drop_index(op.f("ix_feeds_owner_default"), table_name="feeds")
    op.create_index(
        op.f("ix_feeds_is_default"),
        "feeds",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.drop_constraint(op.f("uq_feeds_owner_source_dedup_key"), "feeds", type_="unique")
    op.create_unique_constraint(op.f("uq_feeds_source_dedup_key"), "feeds", ["source_dedup_key"])
    op.drop_constraint(op.f("fk_feeds_owner_users"), "feeds", type_="foreignkey")
    op.drop_column("feeds", "owner")

    op.drop_table("login_attempts")
    op.drop_index(op.f("ix_sessions_expires_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_username"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("users")
