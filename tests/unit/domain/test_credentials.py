"""Feed pairs, API keys and scopes: shape, entropy source, hashing and the scope ladder."""

from __future__ import annotations

import pytest

from copycast.domain import credentials as c
from copycast.domain.enums import KeyScope


def test_feed_pair_shape_and_alphabet() -> None:
    username, password = c.new_feed_username(), c.new_feed_password()
    assert len(username) == c.FEED_USERNAME_LEN and len(password) == c.FEED_PASSWORD_LEN
    assert set(username) <= set(c.FEED_ALPHABET) and set(password) <= set(c.FEED_ALPHABET)
    # No look-alikes and nothing a URL needs to escape.
    assert not set("01lio") & set(c.FEED_ALPHABET)
    assert c.new_feed_password() != password


def test_api_key_shape_hash_and_prefix() -> None:
    secret = c.new_api_key()
    assert secret.startswith(c.API_KEY_PREFIX) and len(secret) == len(c.API_KEY_PREFIX) + 40
    assert c.looks_like_api_key(secret)
    assert not c.looks_like_api_key("cck_") and not c.looks_like_api_key("Basic abc")
    digest = c.api_key_hash(secret)
    assert len(digest) == 64 and digest == c.api_key_hash(secret)
    assert digest != c.api_key_hash(c.new_api_key())
    assert c.api_key_prefix(secret) == secret[:12] and c.api_key_prefix(secret).startswith("cck_")


def test_feed_media_key_is_stable_per_feed_and_password() -> None:
    key = c.feed_media_key("a2617beebed7fefa", "s3cret")
    assert len(key) == c.FEED_MEDIA_KEY_LEN and key.isalnum() and key == key.lower()
    assert key == c.feed_media_key("a2617beebed7fefa", "s3cret")
    assert key != c.feed_media_key("a2617beebed7fefa", "rotated")
    assert key != c.feed_media_key("other", "s3cret")


def test_constant_time_equals() -> None:
    assert c.constant_time_equals("abc", "abc")
    assert not c.constant_time_equals("abc", "abd")
    assert not c.constant_time_equals("abc", "ab")
    assert c.constant_time_equals("é", "é")


@pytest.mark.parametrize(
    ("granted", "required", "allowed"),
    [
        (KeyScope.read, KeyScope.read, True),
        (KeyScope.read, KeyScope.write, False),
        (KeyScope.read, KeyScope.full, False),
        (KeyScope.write, KeyScope.read, True),
        (KeyScope.write, KeyScope.write, True),
        (KeyScope.write, KeyScope.full, False),
        (KeyScope.full, KeyScope.full, True),
    ],
)
def test_scope_ladder(granted: KeyScope, required: KeyScope, allowed: bool) -> None:
    assert c.scope_allows(granted, required) is allowed


def test_scopes_granted_and_required_scope() -> None:
    assert c.scopes_granted(KeyScope.read) == ["read"]
    assert c.scopes_granted(KeyScope.write) == ["read", "write"]
    assert c.scopes_granted(KeyScope.full) == ["read", "write", "full"]
    assert c.required_scope(destructive=True, read_only=False) is KeyScope.full
    assert c.required_scope(destructive=False, read_only=True) is KeyScope.read
    assert c.required_scope(destructive=False, read_only=False) is KeyScope.write


def test_instance_secret_is_32_random_bytes() -> None:
    secret = c.new_instance_secret()
    assert isinstance(secret, bytes) and len(secret) == c.INSTANCE_SECRET_BYTES == 32
    assert c.new_instance_secret() != secret


def test_artwork_mac_matches_its_definition() -> None:
    """The first 26 characters of the lowercase unpadded base32 of
    HMAC-SHA256(secret, "copycast artwork\\0" + asset_id): pinned, since published URLs
    depend on it."""
    import base64
    import hashlib
    import hmac

    secret, asset_id = b"\x01" * 32, "0123456789abcdef"
    digest = hmac.new(secret, b"copycast artwork\x00" + asset_id.encode(), hashlib.sha256)
    expected = base64.b32encode(digest.digest()).decode().lower().rstrip("=")[:26]
    mac = c.artwork_mac(secret, asset_id)
    assert mac == expected and len(mac) == c.ARTWORK_MAC_LEN == 26
    assert set(mac) <= set("abcdefghijklmnopqrstuvwxyz234567")
    # Bound to the asset and to the secret.
    assert c.artwork_mac(secret, "0123456789abcdee") != mac
    assert c.artwork_mac(b"\x02" * 32, asset_id) != mac


def test_artwork_mac_ok_accepts_only_the_exact_mac() -> None:
    secret, asset_id = c.new_instance_secret(), "feedbeef00000001"
    mac = c.artwork_mac(secret, asset_id)
    assert c.artwork_mac_ok(secret, asset_id, mac)
    assert not c.artwork_mac_ok(secret, asset_id, mac.upper())
    assert not c.artwork_mac_ok(secret, asset_id, mac[:-1])
    assert not c.artwork_mac_ok(secret, asset_id, mac + "a")
    assert not c.artwork_mac_ok(secret, asset_id, "")
    assert not c.artwork_mac_ok(secret, "feedbeef00000002", mac)
    assert not c.artwork_mac_ok(c.new_instance_secret(), asset_id, mac)


def test_session_token_shape_and_digest() -> None:
    token = c.mint_session_token()
    assert len(token) == 43 and "=" not in token
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert c.mint_session_token() != token
    digest = c.session_digest(token)
    assert isinstance(digest, bytes) and len(digest) == 32
    assert digest == c.session_digest(token) and digest != c.session_digest(token + "x")


def test_credential_digest_follows_password_and_totp() -> None:
    base = c.credential_digest("$argon2id$v=19$m=65536,t=3,p=4$salt$hash", None)
    assert len(base) == 32
    assert base == c.credential_digest("$argon2id$v=19$m=65536,t=3,p=4$salt$hash", "")
    assert base != c.credential_digest("$argon2id$v=19$m=65536,t=3,p=4$salt$other", None)
    with_totp = c.credential_digest("$argon2id$v=19$m=65536,t=3,p=4$salt$hash", "JBSWY3DPEHPK3PXP")
    assert with_totp != base
    # The TOTP secret is normalised: spaces, case and padding do not change the digest.
    assert with_totp == c.credential_digest(
        "$argon2id$v=19$m=65536,t=3,p=4$salt$hash", "jbsw y3dp ehpk 3pxp=="
    )
    assert c.credential_digest(None, None) == c.credential_digest("", None)
