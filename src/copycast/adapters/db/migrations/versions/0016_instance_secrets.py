"""Instance secrets: the key public artwork tokens are derived with.

Public Artwork moves from ``/feeds/{id}/assets/*.artwork.*`` (a computable URL) to
``/art/{asset_id}-{mac}.{ext}``, the MAC keyed with a secret this table holds. The
api mints it on first start (``INSERT ... ON CONFLICT DO NOTHING``), so the upgrade
creates the table only. The downgrade drops it: the next upgrade mints a new
secret and the artwork URLs change once.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-05 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instance_secrets",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_instance_secrets")),
    )


def downgrade() -> None:
    op.drop_table("instance_secrets")
