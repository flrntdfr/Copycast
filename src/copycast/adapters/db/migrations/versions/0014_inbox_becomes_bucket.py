"""Inboxes become Buckets: ``feeds.kind = 'inbox'`` is now ``'bucket'`` (ADR 0018).

Feed ids, titles and URLs are unchanged, so subscribed podcast apps notice nothing.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-27 22:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _shape(kind: str) -> str:
    return (
        "(kind = 'mirror' AND source_url IS NOT NULL AND source_dedup_key IS NOT NULL "
        "AND source_kind IS NOT NULL AND backfill_mode IS NOT NULL AND autoprune_days IS NULL) "
        f"OR (kind = '{kind}' AND source_url IS NULL AND source_dedup_key IS NULL "
        "AND source_kind IS NULL AND backfill_mode IS NULL)"
    )


def _rename(old: str, new: str) -> None:
    op.drop_constraint(op.f("ck_feeds_kind"), "feeds", type_="check")
    op.drop_constraint(op.f("ck_feeds_kind_shape"), "feeds", type_="check")
    op.execute(f"UPDATE feeds SET kind = '{new}' WHERE kind = '{old}'")
    op.create_check_constraint(op.f("ck_feeds_kind"), "feeds", f"kind IN ('mirror', '{new}')")
    op.create_check_constraint(op.f("ck_feeds_kind_shape"), "feeds", _shape(new))


def upgrade() -> None:
    _rename("inbox", "bucket")


def downgrade() -> None:
    _rename("bucket", "inbox")
