"""The ``users``, ``sessions`` and ``login_attempts`` tables.

``users`` mirrors the configured Users (synced at startup, never deleted) so Feeds,
API keys and jobs can name their Owner with a foreign key; it holds no secret, only a
digest of the configured credentials that tells a changed password apart. ``sessions``
holds the SHA-256 of each browser session token. ``login_attempts`` throttles logins per
typed username, known or not, so it has no foreign key.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, LargeBinary, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from copycast.adapters.db.base import Base, TZDateTime


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    """The last TOTP step accepted: a code is good once."""
    credential_digest: Mapped[bytes | None] = mapped_column(LargeBinary)
    """SHA-256 of the configured password hash and TOTP secret; a change signs sessions out."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.username}>"


class Session(Base):
    __tablename__ = "sessions"

    id_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    username: Mapped[str] = mapped_column(
        Text, ForeignKey("users.username", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_sessions_username", "username"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Session {self.username} until {self.expires_at}>"


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    username: Mapped[str] = mapped_column(Text, primary_key=True)
    failed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LoginAttempt {self.username} x{self.failed_count}>"


__all__ = ["LoginAttempt", "Session", "User"]
