"""Attachments: files the show notes reference, mirrored next to artwork and chapters.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08 00:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.expression import ColumnElement

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "kind IN ('artwork', 'chapters', 'transcript')"
NEW_KINDS = "kind IN ('artwork', 'chapters', 'transcript', 'attachment')"


def _identity_index(with_slot: bool) -> list[str | ColumnElement[Any]]:
    columns: list[str | ColumnElement[Any]] = [
        "feed_id",
        sa.literal_column("coalesce(item_id, '')"),
        "kind",
        sa.literal_column("coalesce(language, '')"),
        sa.literal_column("coalesce(format, '')"),
        "provenance",
    ]
    if with_slot:
        columns.append(sa.literal_column("coalesce(slot, '')"))
    return columns


def upgrade() -> None:
    # The slot tells attachments of one item apart (a hash of the remote URL).
    op.add_column("assets", sa.Column("slot", sa.Text(), nullable=True))
    op.drop_constraint(op.f("ck_assets_kind"), "assets", type_="check")
    op.create_check_constraint(op.f("ck_assets_kind"), "assets", NEW_KINDS)
    op.drop_index("ix_assets_identity", table_name="assets")
    op.create_index("ix_assets_identity", "assets", _identity_index(True), unique=True)


def downgrade() -> None:
    op.execute("DELETE FROM assets WHERE kind = 'attachment'")
    op.drop_index("ix_assets_identity", table_name="assets")
    op.create_index("ix_assets_identity", "assets", _identity_index(False), unique=True)
    op.drop_constraint(op.f("ck_assets_kind"), "assets", type_="check")
    op.create_check_constraint(op.f("ck_assets_kind"), "assets", OLD_KINDS)
    op.drop_column("assets", "slot")
