"""Mirror modes: rolling windows and Automatic (on demand, expiring) archives.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07 20:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MODES = "backfill_mode IN ('all', 'latest', 'selection')"
NEW_MODES = "backfill_mode IN ('all', 'latest', 'selection', 'rolling', 'automatic')"


def upgrade() -> None:
    op.add_column("feeds", sa.Column("retention_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_feeds_retention_days"), "feeds", "retention_days IS NULL OR retention_days > 0"
    )
    op.drop_constraint(op.f("ck_feeds_backfill_mode"), "feeds", type_="check")
    op.create_check_constraint(op.f("ck_feeds_backfill_mode"), "feeds", NEW_MODES)


def downgrade() -> None:
    op.execute(
        "UPDATE feeds SET backfill_mode = 'all' WHERE backfill_mode IN ('rolling', 'automatic')"
    )
    op.drop_constraint(op.f("ck_feeds_backfill_mode"), "feeds", type_="check")
    op.create_check_constraint(op.f("ck_feeds_backfill_mode"), "feeds", OLD_MODES)
    op.drop_constraint(op.f("ck_feeds_retention_days"), "feeds", type_="check")
    op.drop_column("feeds", "retention_days")
