"""The ``instance_secrets`` table: random secrets this installation mints for itself.

One row today, ``artwork``: the key of the public artwork tokens
(``domain.credentials.artwork_mac``). Not in the Object store, so ``copycast
rebuild`` onto an empty database mints a new one and every public artwork URL
changes once; podcast apps refetch images, never audio.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import LargeBinary, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TZDateTime


class InstanceSecret(Base):
    __tablename__ = "instance_secrets"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    """Written once: a secret is never rotated in place, so the row has no ``updated_at``."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InstanceSecret {self.name}>"
