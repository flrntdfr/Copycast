"""Live streams: ``catalog_items.live_status`` records what the Engine said a stream is doing.

Filled by every listing (``is_upcoming`` / ``is_live`` / ``was_live`` from a flat listing)
and by an archive attempt that met a stream still live or still being processed
(``post_live``); NULL when the Source never said. Existing rows start unknown: the next
Refresh fills them from the Source.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE_STATUSES = "live_status IN ('is_upcoming', 'is_live', 'post_live', 'was_live', 'not_live')"


def upgrade() -> None:
    op.add_column("catalog_items", sa.Column("live_status", sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_catalog_items_live_status"), "catalog_items", LIVE_STATUSES)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_catalog_items_live_status"), "catalog_items", type_="check")
    op.drop_column("catalog_items", "live_status")
