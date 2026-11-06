"""The ``staged_uploads`` table: what each Owner holds staged under ``uploads/``.

The Upload itself lives in the Object store (``uploads/{id}/upload.json`` and its file);
this table is only its account, so quota admission and the per-Owner limit read one
indexed sum instead of listing every staged upload of every User. ``bytes`` is the size a
``create_upload`` declared while ``received`` is false, the received size after the PUT.
Rows are short-lived (24 h at most) and not rebuilt from the Object store: an upload
staged before the row existed, or across a rebuild, gets one at its PUT.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Text, false, text
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TZDateTime


class StagedUpload(Base):
    __tablename__ = "staged_uploads"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    received: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)

    __table_args__ = (Index("ix_staged_uploads_owner", "owner"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StagedUpload {self.id} of {self.owner}>"


__all__ = ["StagedUpload"]
