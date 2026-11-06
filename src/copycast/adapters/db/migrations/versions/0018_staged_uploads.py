"""Staged uploads: the per-Owner account of ``uploads/`` (3.0.0-rc.1).

``create_upload`` and ``put_upload`` listed every staged upload of every User (and read
each one's ``upload.json``) to count an Owner's; a row per staged upload makes that one
indexed sum, and lets a failed or refused PUT give its declared size back at once. The
upgrade creates the empty table: uploads already staged get their row at their PUT. The
downgrade drops it.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-06 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staged_uploads",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("received", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staged_uploads")),
    )
    op.create_index("ix_staged_uploads_owner", "staged_uploads", ["owner"])


def downgrade() -> None:
    op.drop_index("ix_staged_uploads_owner", table_name="staged_uploads")
    op.drop_table("staged_uploads")
