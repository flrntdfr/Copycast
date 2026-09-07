"""A Mirror's minimum item length (Shorts stay Available) and preferred metadata language.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("min_duration_seconds", sa.Integer(), nullable=True))
    op.add_column("feeds", sa.Column("preferred_language", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_feeds_min_duration_seconds",
        "feeds",
        "min_duration_seconds IS NULL OR min_duration_seconds > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_feeds_min_duration_seconds", "feeds", type_="check")
    op.drop_column("feeds", "preferred_language")
    op.drop_column("feeds", "min_duration_seconds")
