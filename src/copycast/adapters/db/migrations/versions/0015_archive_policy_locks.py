"""The Archive policy locks what it archives: lock the archived Episodes of Archive Mirrors.

From now on the archive job locks (``archive_flag``) every Episode a Mirror on the Archive
policy (``all``, ``new``, ``latest``, ``selection``) archives, and switching a Mirror to it
locks what it archived so far. This data migration does the same for the Mirrors already
on it. Mirrors that stay in sync with their Source (``sync_deletions``) are left alone: a
lock would stop Sync from deleting what the Source dropped. The downgrade keeps the locks:
nothing tells them from the ones set by hand.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_ARCHIVED = """
UPDATE catalog_items SET archive_flag = true
WHERE archive_state = 'archived'
  AND archive_flag = false
  AND feed_id IN (
    SELECT id FROM feeds
    WHERE kind = 'mirror' AND backfill_mode IN ('all', 'new', 'latest', 'selection')
      AND NOT sync_deletions
  )
"""


BUMP_INTENT = """
UPDATE feeds SET intent_version = intent_version + 1
WHERE kind = 'mirror' AND backfill_mode IN ('all', 'new', 'latest', 'selection')
  AND NOT sync_deletions
  AND EXISTS (
    SELECT 1 FROM catalog_items c WHERE c.feed_id = feeds.id AND c.archive_flag
  )
"""
"""The locks live in ``feed.json`` too: a newer intent makes the next export write them."""


def upgrade() -> None:
    op.execute(LOCK_ARCHIVED)
    op.execute(BUMP_INTENT)


def downgrade() -> None:
    pass
