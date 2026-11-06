"""Credentials Copycast mints: per-feed Basic auth pairs and MCP API keys.

Every secret here is random and high-entropy, so an API key is stored as a
plain SHA-256 digest (no salt or work factor is needed against brute force)
while feed pairs stay in clear text: the UI shows them again for the next
podcast client and ``copycast rebuild`` restores them from the descriptor. A
feed's media key is derived from its password, so it needs no storage of its own.
Public Artwork is published under an opaque token: the asset id plus a MAC under
the instance secret (32 random bytes kept in Postgres), so nobody can compute the
URL of a channel's or a video's artwork to learn that this server mirrors it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

from copycast.domain.enums import KeyScope

FEED_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"
"""Lowercase and digits minus the look-alikes; safe inside ``user:pass@`` URLs."""
FEED_USERNAME_LEN: Final = 8
FEED_PASSWORD_LEN: Final = 24
FEED_MEDIA_KEY_LEN: Final = 22
"""Base32 characters of a feed's media key (110 bits)."""
_MEDIA_KEY_CONTEXT: Final = b"copycast feed media key\x00"
INSTANCE_SECRET_BYTES: Final = 32
ARTWORK_MAC_LEN: Final = 26
"""Base32 characters of an artwork token's MAC (130 bits)."""
_ARTWORK_CONTEXT: Final = b"copycast artwork\x00"
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


def feed_media_key(feed_id: str, password: str) -> str:
    """The secret path segment of a feed's media and asset URLs (``/feeds/{id}/k/{key}/``).

    Players that never answer a Basic challenge (iOS AVPlayer streaming an enclosure)
    follow it. An HMAC of the feed id under the feed's password: rotating the pair
    rotates the key, and ``copycast rebuild`` gets it back with the password.
    """
    mac = hmac.new(
        password.encode("utf-8"), _MEDIA_KEY_CONTEXT + feed_id.encode("utf-8"), hashlib.sha256
    )
    return base64.b32encode(mac.digest()).decode("ascii").lower()[:FEED_MEDIA_KEY_LEN]


def new_instance_secret() -> bytes:
    """The instance secret artwork tokens are keyed with: 32 random bytes."""
    return secrets.token_bytes(INSTANCE_SECRET_BYTES)


def artwork_mac(secret: bytes, asset_id: str) -> str:
    """The MAC half of a public artwork URL (``/art/{asset_id}-{mac}.{ext}``).

    The first 26 characters of the lowercase, unpadded base32 of
    ``HMAC-SHA256(secret, b"copycast artwork\\x00" + asset_id)``.
    """
    mac = hmac.new(secret, _ARTWORK_CONTEXT + asset_id.encode("utf-8"), hashlib.sha256)
    return base64.b32encode(mac.digest()).decode("ascii").lower()[:ARTWORK_MAC_LEN]


def artwork_mac_ok(secret: bytes, asset_id: str, mac: str) -> bool:
    """Whether ``mac`` is :func:`artwork_mac` of ``asset_id``, compared in constant time."""
    return constant_time_equals(mac, artwork_mac(secret, asset_id))


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


SESSION_TOKEN_BYTES: Final = 32


def mint_session_token() -> str:
    """A browser session's secret: 32 random bytes as unpadded base64url (43 characters)."""
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(SESSION_TOKEN_BYTES))
        .decode("ascii")
        .rstrip("=")
    )


def session_digest(token: str) -> bytes:
    """What ``sessions.id_hash`` stores for a session token: its SHA-256 (32 bytes)."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def credential_digest(password_hash: str | None, totp_secret: str | None) -> bytes:
    """A fingerprint of a User's configured credentials (``users.credential_digest``).

    SHA-256 of the password hash and the normalised TOTP secret (spaces and padding
    dropped, upper-cased), one per line. A new password or TOTP secret changes it, which
    signs that User's sessions out at the next start. Users mode only: legacy mode stores
    a salted argon2 hash of the operator password instead (``UserDirectory``), since an
    unsalted SHA-256 of a password is cheap to crack from a database dump.
    """
    totp = "".join((totp_secret or "").split()).rstrip("=").upper()
    return hashlib.sha256(f"{password_hash or ''}\n{totp}".encode()).digest()


__all__ = [
    "API_KEY_PREFIX",
    "ARTWORK_MAC_LEN",
    "FEED_ALPHABET",
    "FEED_MEDIA_KEY_LEN",
    "FEED_PASSWORD_LEN",
    "FEED_USERNAME_LEN",
    "INSTANCE_SECRET_BYTES",
    "SESSION_TOKEN_BYTES",
    "api_key_hash",
    "api_key_prefix",
    "artwork_mac",
    "artwork_mac_ok",
    "constant_time_equals",
    "credential_digest",
    "feed_media_key",
    "looks_like_api_key",
    "mint_session_token",
    "new_api_key",
    "new_feed_password",
    "new_feed_username",
    "new_instance_secret",
    "required_scope",
    "scope_allows",
    "scopes_granted",
    "session_digest",
]
