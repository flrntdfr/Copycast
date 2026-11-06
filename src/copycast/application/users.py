"""The configured Users, read once from the settings.

Roles and quotas always come from here, never from Postgres: ``users`` rows only
name Owners for the foreign keys. Open mode has one implicit Admin and no credential;
legacy mode one Admin whose password is the operator password (hashed lazily, so every
login runs exactly one argon2 verify); users mode the ``[[auth.users]]`` list.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urlsplit
from uuid import UUID

from copycast.application.passwords import PasswordHasher
from copycast.domain.credentials import credential_digest
from copycast.domain.enums import AuthMode, KeyScope, PrincipalVia
from copycast.domain.users import Principal
from copycast.settings import Settings


@dataclass(frozen=True, slots=True)
class DirectoryUser:
    username: str
    admin: bool
    password_hash: str | None
    """None only in open mode."""
    totp_key: bytes | None
    quota_bytes: int | None
    """None means unlimited."""


class UserDirectory:
    """Who may sign in, with which role and quota (``Container.users``)."""

    def __init__(self, settings: Settings) -> None:
        auth = settings.auth
        self._settings = settings
        self.mode: AuthMode = auth.mode
        self.https: bool = urlsplit(settings.base_url).scheme == "https"
        self.owner_of_existing: str = auth.effective_owner_of_existing
        self._login_name = auth.username.strip().lower()

    @cached_property
    def hasher(self) -> PasswordHasher:
        """The dummy verifier: the slowest parameters among the configured hashes."""
        return PasswordHasher.slowest(
            user.password_hash for user in self._configured() if user.password_hash
        )

    @cached_property
    def _legacy_hash(self) -> str | None:
        password = self._settings.auth.password
        return None if password is None else PasswordHasher().hash(password)

    def _configured(self) -> tuple[DirectoryUser, ...]:
        """The users without forcing the legacy hash (``hasher`` needs only real PHCs)."""
        return ()

    def users(self) -> tuple[DirectoryUser, ...]:
        """Open mode: the implicit Admin; legacy: the single Admin (``auth.legacy_owner``)."""
        owner = self._settings.auth.legacy_owner
        if self.mode is AuthMode.legacy:
            return (DirectoryUser(owner, True, self._legacy_hash, None, None),)
        return (DirectoryUser(owner, True, None, None, None),)

    def usernames(self) -> list[str]:
        """The configured usernames, without hashing the legacy password."""
        return [self._settings.auth.legacy_owner]

    def login_name_matches(self, user: DirectoryUser, typed: str) -> bool:
        """``typed`` is stripped and lower-cased; legacy accepts the login name and the slug."""
        if self.mode is AuthMode.legacy:
            return typed in {self._login_name, user.username}
        return typed == user.username

    def credential_digest(self, username: str) -> bytes | None:
        """The fingerprint of ``username``'s configured credentials; None without any."""
        if self.get(username) is None or self.mode is AuthMode.open:
            return None
        return credential_digest(self._settings.auth.password, None)

    def get(self, username: str) -> DirectoryUser | None:
        if username != self._settings.auth.legacy_owner:
            return None
        return self.users()[0]

    def is_configured(self, username: str) -> bool:
        return username == self._settings.auth.legacy_owner

    def quota_bytes(self, username: str) -> int | None:
        """The User's quota; a removed User gets the default (unlimited until users mode)."""
        return None

    def open_principal(self) -> Principal | None:
        """The implicit Admin of open mode; None in every other mode."""
        if self.mode is not AuthMode.open:
            return None
        return Principal.open_admin(self._settings.auth.legacy_owner)

    def principal(
        self,
        username: str,
        *,
        via: PrincipalVia,
        api_key_id: UUID | None = None,
        key_scope: KeyScope | None = None,
    ) -> Principal | None:
        """The Principal of a configured User; None for a removed one."""
        user = self.get(username)
        if user is None:
            return None
        return Principal(
            username=user.username,
            admin=user.admin,
            via=via,
            api_key_id=api_key_id,
            key_scope=key_scope,
        )

    def admin_login_refused(self, user: DirectoryUser) -> bool:
        """Users mode only: an Admin without TOTP on an https site."""
        return self.mode is AuthMode.users and user.admin and self.https and user.totp_key is None

    def startup_warnings(self) -> list[str]:
        warnings: list[str] = []
        owner = self._settings.auth.legacy_owner
        if self.mode is not AuthMode.users and owner != self._login_name:
            warnings.append(
                f"auth.legacy_owner_normalised from={self._login_name} to={owner}: "
                f"the operator's Feeds and settings belong to {owner!r}"
            )
        if self.mode is AuthMode.legacy and self.https:
            warnings.append(
                "auth.legacy_without_totp: the operator password is the only factor on an "
                "https site; configure [[auth.users]] with a totp_secret"
            )
        return warnings


__all__ = ["DirectoryUser", "UserDirectory"]
