"""Playlist captures: Mirrors of YouTube playlists that live on the Inboxes screen.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-08 21:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feeds",
        sa.Column(
            "playlist_capture", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("feeds", "playlist_capture")
