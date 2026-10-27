"""Serial Inboxes: ``feeds.serial`` marks an Inbox built from uploads in a given order (ADR 0015).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-27 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feeds",
        sa.Column("serial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("feeds", "serial")
