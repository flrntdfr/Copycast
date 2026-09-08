"""Per-Mirror Refresh interval (hours) and playlist sync (delete what the Source drops).

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-08 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("refresh_interval_hours", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_feeds_refresh_interval_hours"),
        "feeds",
        "refresh_interval_hours IS NULL OR refresh_interval_hours > 0",
    )
    op.add_column(
        "feeds",
        sa.Column("sync_deletions", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("feeds", "sync_deletions")
    op.drop_constraint(op.f("ck_feeds_refresh_interval_hours"), "feeds", type_="check")
    op.drop_column("feeds", "refresh_interval_hours")
