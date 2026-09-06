"""Initial schema (Copycast 1.0.0).

Revision ID: 0001
Revises:
Create Date: 2026-09-05 08:02:23.009432+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("git_head", sa.Text(), nullable=True),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engine_versions")),
        sa.UniqueConstraint("version", name=op.f("uq_engine_versions_version")),
    )
    op.create_table(
        "feeds",
        sa.Column("id", sa.String(length=63), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("artwork_url", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_dedup_key", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.Text(), nullable=True),
        sa.Column("service", sa.Text(), nullable=True),
        sa.Column("source_etag", sa.Text(), nullable=True),
        sa.Column("source_last_modified", sa.Text(), nullable=True),
        sa.Column("backfill_mode", sa.Text(), nullable=True),
        sa.Column("backfill_latest_n", sa.Integer(), nullable=True),
        sa.Column("follow", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("policy_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "engine_options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("autoprune_days", sa.Integer(), nullable=True),
        sa.Column("last_autoprune_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_channel_xml", sa.Text(), nullable=True),
        sa.Column("last_refresh_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("storage_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("intent_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'mirror' AND source_url IS NOT NULL AND source_dedup_key IS NOT NULL "
            "AND source_kind IS NOT NULL AND backfill_mode IS NOT NULL "
            "AND autoprune_days IS NULL) "
            "OR (kind = 'inbox' AND source_url IS NULL AND source_dedup_key IS NULL "
            "AND source_kind IS NULL AND backfill_mode IS NULL)",
            name=op.f("ck_feeds_kind_shape"),
        ),
        sa.CheckConstraint(
            "backfill_mode IN ('all', 'latest', 'selection')", name=op.f("ck_feeds_backfill_mode")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(engine_options) = 'object'", name=op.f("ck_feeds_engine_options_object")
        ),
        sa.CheckConstraint("kind IN ('mirror', 'inbox')", name=op.f("ck_feeds_kind")),
        sa.CheckConstraint("source_kind IN ('rss', 'ytdlp')", name=op.f("ck_feeds_source_kind")),
        sa.CheckConstraint(
            "autoprune_days IS NULL OR autoprune_days > 0", name=op.f("ck_feeds_autoprune_days")
        ),
        sa.CheckConstraint(
            "backfill_latest_n IS NULL OR backfill_latest_n > 0",
            name=op.f("ck_feeds_backfill_latest_n"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feeds")),
        sa.UniqueConstraint("source_dedup_key", name=op.f("uq_feeds_source_dedup_key")),
    )
    op.create_index(
        "ix_feeds_is_default",
        "feeds",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_index(
        "ix_feeds_lower_title", "feeds", [sa.literal_column("lower(title)")], unique=False
    )
    op.create_table(
        "worker_heartbeat",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "status",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_id", name=op.f("pk_worker_heartbeat")),
    )
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("feed_id", sa.String(length=63), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_number", sa.Integer(), nullable=True),
        sa.Column("source_season", sa.Integer(), nullable=True),
        sa.Column("source_position", sa.Integer(), nullable=True),
        sa.Column("tab", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("artwork_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("archivable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source_item_xml", sa.Text(), nullable=True),
        sa.Column("listed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_listed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "archive_state", sa.Text(), server_default=sa.text("'available'"), nullable=False
        ),
        sa.Column("wanted_reason", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_path", sa.Text(), nullable=True),
        sa.Column("media_mime", sa.Text(), nullable=True),
        sa.Column("media_bytes", sa.BigInteger(), nullable=True),
        sa.Column("download_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "archive_state IN ('available', 'wanted', 'archiving', 'archived', 'failed', "
            "'deleted')",
            name=op.f("ck_catalog_items_archive_state"),
        ),
        sa.CheckConstraint(
            "wanted_reason IN ('backfill', 'follow', 'manual', 'request')",
            name=op.f("ck_catalog_items_wanted_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["feed_id"],
            ["feeds.id"],
            name=op.f("fk_catalog_items_feed_id_feeds"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_items")),
        sa.UniqueConstraint("feed_id", "ordinal", name=op.f("uq_catalog_items_feed_id_ordinal")),
        sa.UniqueConstraint(
            "feed_id", "source_key", name=op.f("uq_catalog_items_feed_id_source_key")
        ),
    )
    op.create_index(
        "ix_catalog_items_feed_first_downloaded",
        "catalog_items",
        ["feed_id", "first_downloaded_at"],
        unique=False,
        postgresql_where=sa.text("archive_state = 'archived'"),
    )
    op.create_index(
        "ix_catalog_items_feed_published",
        "catalog_items",
        [
            "feed_id",
            sa.literal_column("published_at DESC NULLS LAST"),
            sa.literal_column("ordinal DESC"),
        ],
        unique=False,
    )
    op.create_index(
        "ix_catalog_items_feed_source_number",
        "catalog_items",
        ["feed_id", "source_number"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_items_feed_state", "catalog_items", ["feed_id", "archive_state"], unique=False
    )
    op.create_index(
        "ix_catalog_items_pending",
        "catalog_items",
        ["archive_state"],
        unique=False,
        postgresql_where=sa.text("archive_state IN ('wanted', 'failed')"),
    )
    op.create_table(
        "requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("feed_id", sa.String(length=63), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("requested_via", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expanded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "requested_via IN ('ui', 'mcp')", name=op.f("ck_requests_requested_via")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'expanded', 'failed')", name=op.f("ck_requests_status")
        ),
        sa.ForeignKeyConstraint(
            ["feed_id"], ["feeds.id"], name=op.f("fk_requests_feed_id_feeds"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requests")),
    )
    op.create_index(
        "ix_requests_feed_created",
        "requests",
        ["feed_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("feed_id", sa.String(length=63), nullable=False),
        sa.Column("item_id", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'mirrored'"), nullable=False),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("format", sa.Text(), nullable=True),
        sa.Column("remote_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("mime", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'wanted'"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "format IN ('vtt', 'srt', 'json', 'text', 'html')", name=op.f("ck_assets_format")
        ),
        sa.CheckConstraint(
            "kind IN ('artwork', 'chapters', 'transcript')", name=op.f("ck_assets_kind")
        ),
        sa.CheckConstraint(
            "provenance IN ('mirrored', 'generated')", name=op.f("ck_assets_provenance")
        ),
        sa.CheckConstraint(
            "state IN ('wanted', 'archived', 'failed')", name=op.f("ck_assets_state")
        ),
        sa.ForeignKeyConstraint(
            ["feed_id"], ["feeds.id"], name=op.f("fk_assets_feed_id_feeds"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["catalog_items.id"],
            name=op.f("fk_assets_item_id_catalog_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    op.create_index(
        "ix_assets_identity",
        "assets",
        [
            "feed_id",
            sa.literal_column("coalesce(item_id, '')"),
            "kind",
            sa.literal_column("coalesce(language, '')"),
            sa.literal_column("coalesce(format, '')"),
            "provenance",
        ],
        unique=True,
    )
    op.create_index("ix_assets_item_id", "assets", ["item_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("feed_id", sa.String(length=63), nullable=True),
        sa.Column("item_id", sa.String(length=16), nullable=True),
        sa.Column("request_id", sa.UUID(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_kind", sa.Text(), nullable=True),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("engine_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "error_kind IN ('transient', 'permanent', 'cancelled', 'storage_full')",
            name=op.f("ck_jobs_error_kind"),
        ),
        sa.CheckConstraint(
            "kind IN ('refresh', 'archive_item', 'expand_request', 'prune', 'rebuild')",
            name=op.f("ck_jobs_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_jobs_status"),
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'feed_fetch', 'request', 'policy', 'ui', 'mcp')",
            name=op.f("ck_jobs_trigger"),
        ),
        sa.ForeignKeyConstraint(
            ["feed_id"], ["feeds.id"], name=op.f("fk_jobs_feed_id_feeds"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["catalog_items.id"],
            name=op.f("fk_jobs_item_id_catalog_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_jobs_request_id_requests"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_dedup_active",
        "jobs",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_feed_created",
        "jobs",
        ["feed_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index("ix_jobs_item_id", "jobs", ["item_id"], unique=False)
    op.create_index("ix_jobs_queue", "jobs", ["status", "run_after", "priority"], unique=False)
    op.create_index("ix_jobs_request_id", "jobs", ["request_id"], unique=False)
    op.create_table(
        "request_items",
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["catalog_items.id"],
            name=op.f("fk_request_items_item_id_catalog_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_request_items_request_id_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id", "item_id", name=op.f("pk_request_items")),
    )
    op.create_index("ix_request_items_item_id", "request_items", ["item_id"], unique=False)
    op.create_table(
        "job_log_lines",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level IN ('debug', 'info', 'warning', 'error')", name=op.f("ck_job_log_lines_level")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_log_lines_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", "seq", name=op.f("pk_job_log_lines")),
    )
    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("feed_id", sa.String(length=63), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("listed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("new_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("delisted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("wanted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("engine_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'unchanged', 'failed', 'cancelled')",
            name=op.f("ck_refresh_runs_status"),
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'feed_fetch', 'request', 'policy', 'ui', 'mcp')",
            name=op.f("ck_refresh_runs_trigger"),
        ),
        sa.ForeignKeyConstraint(
            ["feed_id"],
            ["feeds.id"],
            name=op.f("fk_refresh_runs_feed_id_feeds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_refresh_runs_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_runs")),
    )
    op.create_index(
        "ix_refresh_runs_feed_started",
        "refresh_runs",
        ["feed_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index("ix_refresh_runs_job_id", "refresh_runs", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_refresh_runs_job_id", table_name="refresh_runs")
    op.drop_index("ix_refresh_runs_feed_started", table_name="refresh_runs")
    op.drop_table("refresh_runs")
    op.drop_table("job_log_lines")
    op.drop_index("ix_request_items_item_id", table_name="request_items")
    op.drop_table("request_items")
    op.drop_index("ix_jobs_request_id", table_name="jobs")
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_index("ix_jobs_item_id", table_name="jobs")
    op.drop_index("ix_jobs_feed_created", table_name="jobs")
    op.drop_index(
        "ix_jobs_dedup_active",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_table("jobs")
    op.drop_index("ix_assets_item_id", table_name="assets")
    op.drop_index("ix_assets_identity", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_requests_feed_created", table_name="requests")
    op.drop_table("requests")
    op.drop_index(
        "ix_catalog_items_pending",
        table_name="catalog_items",
        postgresql_where=sa.text("archive_state IN ('wanted', 'failed')"),
    )
    op.drop_index("ix_catalog_items_feed_state", table_name="catalog_items")
    op.drop_index("ix_catalog_items_feed_source_number", table_name="catalog_items")
    op.drop_index("ix_catalog_items_feed_published", table_name="catalog_items")
    op.drop_index(
        "ix_catalog_items_feed_first_downloaded",
        table_name="catalog_items",
        postgresql_where=sa.text("archive_state = 'archived'"),
    )
    op.drop_table("catalog_items")
    op.drop_table("worker_heartbeat")
    op.drop_index("ix_feeds_lower_title", table_name="feeds")
    op.drop_index("ix_feeds_is_default", table_name="feeds", postgresql_where=sa.text("is_default"))
    op.drop_table("feeds")
    op.drop_table("engine_versions")
