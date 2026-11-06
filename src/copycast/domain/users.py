"""Users and Principals: who a request or a job acts as.

A User comes from the configuration (``[[auth.users]]``), or is the one implicit Admin
of the open and legacy auth modes. Usernames are the stable identity: they name a Feed's
Owner and the Object store prefix of that User's settings (``users/{username}/``), so
they are held to a narrow, path-safe pattern. The legacy operator login name is not;
:func:`owner_slug` maps it onto that pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Final
from uuid import UUID

from copycast.domain.enums import KeyScope, PrincipalVia

USERNAME_PATTERN: Final = r"^[a-z0-9][a-z0-9._-]{0,31}$"
USERNAME_RE: Final = re.compile(USERNAME_PATTERN)
USERNAME_MAX_LEN: Final = 32
FALLBACK_OWNER: Final = "copycast"
"""The owner name a login name with nothing usable in it maps to."""

_OUTSIDE = re.compile(r"[^a-z0-9._-]+")
_LEADING = re.compile(r"^[^a-z0-9]+")


def is_valid_username(value: str) -> bool:
    """1-32 lowercase letters, digits, ``.``, ``_`` or ``-``, starting with a letter or digit."""
    return USERNAME_RE.fullmatch(value) is not None


def owner_slug(login_name: str) -> str:
    """The owner name and Object store path segment of an open- or legacy-mode Admin.

    Deterministic: the stripped, lower-cased name when it is already a valid username;
    otherwise every run of characters outside ``[a-z0-9._-]`` becomes ``-``, leading
    characters outside ``[a-z0-9]`` go, the result is cut to 32 characters, and an empty
    result falls back to ``copycast``. ``"Florent"`` gives ``florent`` and
    ``"me@example.org"`` gives ``me-example.org``.
    """
    name = login_name.strip().lower()
    if is_valid_username(name):
        return name
    name = _LEADING.sub("", _OUTSIDE.sub("-", name))[:USERNAME_MAX_LEN]
    return name if is_valid_username(name) else FALLBACK_OWNER


@dataclass(frozen=True, slots=True)
class Principal:
    """Whoever a request or a job acts as.

    ``username`` is None only for :attr:`SYSTEM` (the worker, the scheduler, Light
    Refresh and the public routes, whose credential is checked elsewhere). An API key
    Principal carries the key's id and scope.
    """

    username: str | None
    admin: bool
    via: PrincipalVia
    api_key_id: UUID | None = None
    key_scope: KeyScope | None = None

    SYSTEM: ClassVar[Principal]

    @property
    def is_system(self) -> bool:
        return self.via is PrincipalVia.system

    @classmethod
    def open_admin(cls, username: str) -> Principal:
        """The one implicit Admin of open mode."""
        return cls(username=username, admin=True, via=PrincipalVia.open)


Principal.SYSTEM = Principal(username=None, admin=True, via=PrincipalVia.system)


__all__ = [
    "FALLBACK_OWNER",
    "USERNAME_MAX_LEN",
    "USERNAME_PATTERN",
    "USERNAME_RE",
    "Principal",
    "is_valid_username",
    "owner_slug",
]
