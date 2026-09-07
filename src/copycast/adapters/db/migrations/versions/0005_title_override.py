"""A Mirror's own title, kept over the Source's across Refreshes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-07 22:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("title_override", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("feeds", "title_override")
