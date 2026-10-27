"""The Archive policy without backfill (``backfill_mode = 'new'``) and the Archive flag.

``new`` archives only what the Source lists after the policy is applied.
``catalog_items.archive_flag`` marks an Episode kept for good: Retention, a Rolling window,
Sync and clearing a Feed spare it. Existing rows start unflagged.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27 20:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_MODES = "backfill_mode IN ('all', 'latest', 'selection', 'rolling', 'automatic')"
NEW_MODES = "backfill_mode IN ('all', 'latest', 'selection', 'rolling', 'automatic', 'new')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_feeds_backfill_mode"), "feeds", type_="check")
    op.create_check_constraint(op.f("ck_feeds_backfill_mode"), "feeds", NEW_MODES)
    op.add_column(
        "catalog_items",
        sa.Column("archive_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("catalog_items", "archive_flag")
    op.execute("UPDATE feeds SET backfill_mode = 'all' WHERE backfill_mode = 'new'")
    op.drop_constraint(op.f("ck_feeds_backfill_mode"), "feeds", type_="check")
    op.create_check_constraint(op.f("ck_feeds_backfill_mode"), "feeds", OLD_MODES)
