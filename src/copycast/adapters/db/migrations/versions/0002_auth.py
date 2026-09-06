"""Per-feed Basic auth pairs and MCP API keys.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from copycast.domain.credentials import new_feed_password, new_feed_username

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("feeds", sa.Column("auth_username", sa.String(length=16), nullable=True))
    op.add_column("feeds", sa.Column("auth_password", sa.Text(), nullable=True))
    # Every existing feed gets its own pair; the columns become NOT NULL afterwards.
    conn = op.get_bind()
    feed_ids = conn.execute(sa.text("SELECT id FROM feeds")).scalars().all()
    for feed_id in feed_ids:
        conn.execute(
            sa.text(
                "UPDATE feeds SET auth_username = :username, auth_password = :password "
                "WHERE id = :id"
            ),
            {"username": new_feed_username(), "password": new_feed_password(), "id": feed_id},
        )
    op.alter_column("feeds", "auth_username", nullable=False)
    op.alter_column("feeds", "auth_password", nullable=False)

    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("scope IN ('read', 'write', 'full')", name=op.f("ck_api_keys_scope")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_column("feeds", "auth_password")
    op.drop_column("feeds", "auth_username")
