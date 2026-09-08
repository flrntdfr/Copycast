"""Whether an item's date is approximate (a flat YouTube listing's "3 weeks ago").

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_items",
        sa.Column(
            "published_at_approximate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("catalog_items", "published_at_approximate")
