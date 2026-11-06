"""The configured Users, read once from the settings.

Roles and quotas always come from here, never from Postgres: ``users`` rows only
name Owners for the foreign keys. Open mode has one implicit Admin and no credential;
legacy mode one Admin whose password is the operator password (hashed lazily, so every
login runs exactly one argon2 verify; ``sync_users`` does it at startup, off the event
loop); users mode the ``[[auth.users]]`` list. The operator password never reaches
Postgres except as a salted argon2 hash (``credential_digest``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import urlsplit
from uuid import UUID

from copycast.application.passwords import PasswordHasher, hash_parameters
from copycast.domain.credentials import credential_digest
from copycast.domain.enums import AuthMode, KeyScope, PrincipalVia
from copycast.domain.quota import quota_bytes
from copycast.domain.totp import decode_secret
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
        self._configured_users: tuple[DirectoryUser, ...] = tuple(
            DirectoryUser(
                username=user.username,
                admin=user.admin,
                password_hash=user.password_hash,
                totp_key=decode_secret(user.totp_secret) if user.totp_secret else None,
                quota_bytes=quota_bytes(auth.quota_for(user.username)),
            )
            for user in auth.users
        )
        self._by_name = {user.username: user for user in self._configured_users}

    @cached_property
    def hasher(self) -> PasswordHasher:
        """The dummy verifier: the slowest parameters among the configured hashes."""
        return PasswordHasher.slowest(
            user.password_hash for user in self._configured_users if user.password_hash
        )

    @cached_property
    def _legacy_hash(self) -> str | None:
        password = self._settings.auth.password
        return None if password is None else PasswordHasher().hash(password)

    @property
    def _implicit_quota(self) -> int | None:
        return quota_bytes(self._settings.auth.default_quota)

    def users(self) -> tuple[DirectoryUser, ...]:
        """Users mode: ``[[auth.users]]``; open mode: the implicit Admin; legacy: the single
        Admin (``auth.legacy_owner``), whose hash is computed on first use."""
        if self.mode is AuthMode.users:
            return self._configured_users
        owner = self._settings.auth.legacy_owner
        password_hash = self._legacy_hash if self.mode is AuthMode.legacy else None
        return (DirectoryUser(owner, True, password_hash, None, self._implicit_quota),)

    def usernames(self) -> list[str]:
        """The configured usernames, without hashing the legacy password."""
        if self.mode is AuthMode.users:
            return [user.username for user in self._configured_users]
        return [self._settings.auth.legacy_owner]

    def login_name_matches(self, user: DirectoryUser, typed: str) -> bool:
        """``typed`` is stripped and lower-cased; legacy accepts the login name and the slug."""
        if self.mode is AuthMode.legacy:
            return typed in {self._login_name, user.username}
        return typed == user.username

    def login_username(self, typed: str) -> str | None:
        """The username a typed login name (stripped, lower-cased) names, if any.

        Never hashes the legacy password (that happens on the first verify, in a thread).
        """
        if self.mode is AuthMode.open:
            return None
        if self.mode is AuthMode.users:
            return typed if typed in self._by_name else None
        owner = self._settings.auth.legacy_owner
        return owner if typed in {self._login_name, owner} else None

    def credential_digest(self, username: str) -> bytes | None:
        """The fingerprint of ``username``'s configured credentials; None without any.

        Users mode: SHA-256 of the configured argon2 hash and TOTP secret. Legacy mode: an
        argon2id hash of the operator password (salted and slow, so a database reader
        cannot crack it cheaply): computing it hashes, so run it on the hash limiter.
        """
        if self.mode is AuthMode.open or not self.is_configured(username):
            return None
        if self.mode is AuthMode.legacy:
            legacy_hash = self._legacy_hash
            return None if legacy_hash is None else legacy_hash.encode("ascii")
        config = self._settings.auth.user(username)
        assert config is not None
        return credential_digest(config.password_hash, config.totp_secret)

    def credential_matches(self, username: str, stored: bytes | None) -> bool:
        """Whether ``stored`` is still the fingerprint of ``username``'s credentials.

        Legacy mode verifies the operator password against the stored argon2 hash (run it
        on the hash limiter); users mode compares digests.
        """
        if stored is None or self.mode is AuthMode.open or not self.is_configured(username):
            return False
        if self.mode is AuthMode.legacy:
            password = self._settings.auth.password
            phc = stored.decode("ascii", errors="replace")
            return (
                password is not None
                and phc.startswith("$argon2id$")
                and self.hasher.verify(phc, password)
            )
        return stored == self.credential_digest(username)

    def changed_credentials(self, stored: Mapping[str, bytes | None]) -> dict[str, bytes]:
        """The new fingerprint of every configured User whose ``stored`` one no longer
        matches (``sync_users``). Hashes in legacy mode: run it on the hash limiter.

        Legacy mode also hashes the operator password here, once per process, so that no
        later call (``users()``, ``get()``) ever hashes it on the event loop.
        """
        if self.mode is AuthMode.legacy:
            _ = self._legacy_hash
        changed: dict[str, bytes] = {}
        for name in self.usernames():
            if self.credential_matches(name, stored.get(name)):
                continue
            digest = self.credential_digest(name)
            if digest is not None:
                changed[name] = digest
        return changed

    def has_totp(self, username: str) -> bool:
        """Whether ``username`` signs in with a TOTP code; never hashes anything."""
        if self.mode is not AuthMode.users:
            return False
        user = self._by_name.get(username)
        return user is not None and user.totp_key is not None

    def get(self, username: str) -> DirectoryUser | None:
        if self.mode is AuthMode.users:
            return self._by_name.get(username)
        if username != self._settings.auth.legacy_owner:
            return None
        return self.users()[0]

    def is_configured(self, username: str) -> bool:
        if self.mode is AuthMode.users:
            return username in self._by_name
        return username == self._settings.auth.legacy_owner

    def quota_bytes(self, username: str) -> int | None:
        """The User's quota in bytes (None: unlimited); a removed User gets the default."""
        return quota_bytes(self._settings.auth.quota_for(username))

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
        if not self.is_configured(username):
            return None
        admin = True if self.mode is not AuthMode.users else self._by_name[username].admin
        return Principal(
            username=username,
            admin=admin,
            via=via,
            api_key_id=api_key_id,
            key_scope=key_scope,
        )

    def admin_login_refused(self, user: DirectoryUser) -> bool:
        """Users mode only: an Admin without TOTP on an https site."""
        return self.mode is AuthMode.users and user.admin and self.https and user.totp_key is None

    def startup_warnings(self) -> list[str]:
        """One line per thing an operator should fix; logged once at startup."""
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
        for user in self._configured_users:
            if self.admin_login_refused(user):
                warnings.append(
                    f"auth.admin_without_totp username={user.username}: an Admin needs a "
                    "totp_secret on an https site, so their sign-in is refused (their API keys "
                    "keep working); run: copycast users totp " + user.username
                )
        parameters = {
            hash_parameters(user.password_hash)
            for user in self._configured_users
            if user.password_hash is not None
        }
        if len(parameters) > 1:
            warnings.append(
                "auth.hash_parameters_differ: the configured password hashes use different "
                "argon2 parameters, so a sign-in answers faster for some users than for an "
                "unknown name; hash every password with copycast users hash-password"
            )
        return warnings


__all__ = ["DirectoryUser", "UserDirectory"]
