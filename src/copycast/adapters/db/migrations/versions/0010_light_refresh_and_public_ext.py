"""Light Refresh on feed fetch and a stable public media extension per item.

``feeds.last_light_refresh_at`` stamps the last inline (feed-fetch) light Refresh so its
cooldown never pushes back the scheduled full Refresh; ``refresh_runs.light`` tells light
runs apart from full ones; ``catalog_items.public_ext`` is the extension of the media URL
the feed advertises, fixed at listing time so it never changes when an Episode is archived
or expires (podcast apps re-download on a URL change). Existing archived rows keep the
extension of their file, unarchived rows get the prediction for their Source.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-17 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feeds", sa.Column("last_light_refresh_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "refresh_runs",
        sa.Column("light", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "catalog_items",
        sa.Column("public_ext", sa.Text(), nullable=False, server_default=sa.text("'mp3'")),
    )
    # Archived rows keep the extension podcast apps already hold; the rest get the
    # prediction for their Source (YouTube and other engine Sources archive to m4a).
    op.execute(
        sa.text(
            """
            UPDATE catalog_items AS c
            SET public_ext = CASE
                WHEN c.media_path IS NOT NULL AND c.media_path LIKE '%.%'
                    THEN lower(substring(c.media_path from '\\.([A-Za-z0-9]{1,8})$'))
                WHEN f.source_kind = 'ytdlp' THEN 'm4a'
                ELSE 'mp3'
            END
            FROM feeds AS f
            WHERE f.id = c.feed_id
            """
        )
    )
    op.execute(sa.text("UPDATE catalog_items SET public_ext = 'mp3' WHERE public_ext = ''"))


def downgrade() -> None:
    op.drop_column("catalog_items", "public_ext")
    op.drop_column("refresh_runs", "light")
    op.drop_column("feeds", "last_light_refresh_at")
