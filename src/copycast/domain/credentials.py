"""Credentials Copycast mints: per-feed Basic auth pairs and MCP API keys.

Every secret here is random and high-entropy, so an API key is stored as a
plain SHA-256 digest (no salt or work factor is needed against brute force)
while feed pairs stay in clear text: the UI shows them again for the next
podcast client and ``copycast rebuild`` restores them from the descriptor.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Final

from copycast.domain.enums import KeyScope

FEED_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"
"""Lowercase and digits minus the look-alikes; safe inside ``user:pass@`` URLs."""
FEED_USERNAME_LEN: Final = 8
FEED_PASSWORD_LEN: Final = 24
API_KEY_PREFIX: Final = "cck_"
API_KEY_BYTES: Final = 30
API_KEY_DISPLAY_LEN: Final = 12
"""``cck_`` plus eight characters: enough to tell keys apart, useless to guess the rest."""

_SCOPE_RANK: Final[dict[KeyScope, int]] = {
    KeyScope.read: 0,
    KeyScope.write: 1,
    KeyScope.full: 2,
}


def _token(length: int) -> str:
    return "".join(secrets.choice(FEED_ALPHABET) for _ in range(length))


def new_feed_username() -> str:
    """Eight characters from the unambiguous alphabet; an identifier, not a secret."""
    return _token(FEED_USERNAME_LEN)


def new_feed_password() -> str:
    """Twenty-four characters (about 119 bits) from the unambiguous alphabet."""
    return _token(FEED_PASSWORD_LEN)


def new_api_key() -> str:
    """``cck_`` plus 40 URL-safe characters; shown once, stored as :func:`api_key_hash`."""
    return API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_BYTES)


def api_key_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def api_key_prefix(secret: str) -> str:
    """The displayable head of a key (``cck_XXXXXXXX``)."""
    return secret[:API_KEY_DISPLAY_LEN]


def looks_like_api_key(value: str) -> bool:
    return value.startswith(API_KEY_PREFIX) and len(value) > API_KEY_DISPLAY_LEN


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def scope_allows(granted: KeyScope, required: KeyScope) -> bool:
    """``read`` keys read, ``write`` keys also change things, ``full`` keys also delete."""
    return _SCOPE_RANK[granted] >= _SCOPE_RANK[required]


def scopes_granted(scope: KeyScope) -> list[str]:
    """Every scope name a key of ``scope`` carries (``full`` -> read, write, full)."""
    rank = _SCOPE_RANK[scope]
    return [name.value for name, value in _SCOPE_RANK.items() if value <= rank]


def required_scope(*, destructive: bool, read_only: bool) -> KeyScope:
    """The scope a tool needs: destructive -> full, read-only -> read, otherwise write."""
    if destructive:
        return KeyScope.full
    if read_only:
        return KeyScope.read
    return KeyScope.write


__all__ = [
    "API_KEY_PREFIX",
    "FEED_ALPHABET",
    "FEED_PASSWORD_LEN",
    "FEED_USERNAME_LEN",
    "api_key_hash",
    "api_key_prefix",
    "constant_time_equals",
    "looks_like_api_key",
    "new_api_key",
    "new_feed_password",
    "new_feed_username",
    "required_scope",
    "scope_allows",
    "scopes_granted",
]
