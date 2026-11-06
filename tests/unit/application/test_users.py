"""The UserDirectory in open, legacy and users mode, and the password hasher."""

from __future__ import annotations

import hashlib
from typing import Any

from copycast.application.passwords import PasswordHasher, hash_parameters
from copycast.application.users import DirectoryUser, UserDirectory
from copycast.domain.credentials import credential_digest
from copycast.domain.enums import AuthMode, KeyScope, PrincipalVia
from copycast.domain.totp import decode_secret
from copycast.domain.users import Principal
from copycast.settings import Settings, get_settings
from tests.support.users import (
    TEST_HASHER,
    TOTP_SECRET,
    USERS,
    password_hash,
    user_entry,
    users_auth,
)


def _settings(**auth: Any) -> Settings:
    return get_settings(base_url="http://testserver", auth=auth)


def test_open_mode_has_one_implicit_admin() -> None:
    directory = UserDirectory(_settings())
    assert directory.mode is AuthMode.open and not directory.https
    assert directory.owner_of_existing == "copycast"
    assert directory.users() == (DirectoryUser("copycast", True, None, None, None),)
    assert directory.usernames() == ["copycast"]
    assert directory.open_principal() == Principal.open_admin("copycast")
    assert directory.credential_digest("copycast") is None
    assert directory.is_configured("copycast") and not directory.is_configured("alice")
    assert directory.get("alice") is None and directory.quota_bytes("copycast") is None
    assert directory.startup_warnings() == []
    assert directory.hasher.memory_cost > 0


def test_legacy_mode_hashes_the_operator_password_lazily() -> None:
    directory = UserDirectory(_settings(username="Florent", password="correct horse battery"))
    assert directory.mode is AuthMode.legacy
    assert directory.owner_of_existing == "florent"
    assert directory.open_principal() is None
    assert directory.usernames() == ["florent"]
    assert "_legacy_hash" not in vars(directory)
    (user,) = directory.users()
    assert user.username == "florent" and user.admin and user.totp_key is None
    assert user.password_hash is not None and user.password_hash.startswith("$argon2id$")
    assert directory.get("florent") == user
    # The login name as configured (any case) and the owner slug both match.
    assert directory.login_name_matches(user, "florent")
    assert not directory.login_name_matches(user, "copycast")
    assert directory.credential_digest("nobody") is None
    assert not directory.has_totp("florent")
    assert not directory.admin_login_refused(user)
    warnings = directory.startup_warnings()
    assert len(warnings) == 0  # "florent" is already the slug of "Florent"


def test_legacy_login_name_that_is_not_a_username() -> None:
    directory = UserDirectory(_settings(username="Me@Example.org", password="x" * 12))
    (user,) = directory.users()
    assert user.username == "me-example.org"
    assert directory.login_name_matches(user, "me@example.org")
    assert directory.login_name_matches(user, "me-example.org")
    assert any("legacy_owner_normalised" in w for w in directory.startup_warnings())


def test_legacy_on_https_warns_about_the_single_factor() -> None:
    settings = get_settings(base_url="https://copycast.example", auth={"password": "x" * 12})
    directory = UserDirectory(settings)
    assert directory.https
    assert any("legacy_without_totp" in w for w in directory.startup_warnings())


def test_principal_of_a_configured_user_only() -> None:
    directory = UserDirectory(_settings())
    key = directory.principal("copycast", via=PrincipalVia.api_key, key_scope=KeyScope.read)
    assert key is not None and key.admin and key.key_scope is KeyScope.read
    assert key.via is PrincipalVia.api_key
    assert directory.principal("removed", via=PrincipalVia.session) is None


# --------------------------------------------------------------------------- users mode


def _users_settings(base_url: str = "http://testserver", **auth: Any) -> Settings:
    return get_settings(base_url=base_url, auth={**users_auth(), **auth})


def test_users_mode_reads_roles_quotas_and_totp_from_the_config() -> None:
    settings = get_settings(
        base_url="http://testserver",
        auth={
            "users": [
                user_entry(USERS["root"], totp_secret=TOTP_SECRET, quota="unlimited"),
                user_entry(USERS["alice"], quota="1 GB"),
                user_entry(USERS["bob"]),
            ],
            "default_quota": "10 GB",
        },
    )
    directory = UserDirectory(settings)
    assert directory.mode is AuthMode.users
    assert directory.owner_of_existing == "root"
    assert directory.usernames() == ["root", "alice", "bob"]
    root, alice, bob = directory.users()
    assert root.admin and not alice.admin and not bob.admin
    assert root.totp_key == decode_secret(TOTP_SECRET) and alice.totp_key is None
    assert (root.quota_bytes, alice.quota_bytes, bob.quota_bytes) == (
        None,
        1_000_000_000,
        10_000_000_000,
    )
    assert directory.quota_bytes("removed") == 10_000_000_000
    assert directory.get("alice") == alice and directory.get("removed") is None
    assert directory.is_configured("bob") and not directory.is_configured("copycast")
    assert directory.open_principal() is None
    # Login names are exact usernames in users mode.
    assert directory.login_username("alice") == "alice"
    assert directory.login_username("copycast") is None
    assert directory.login_name_matches(alice, "alice")
    assert not directory.login_name_matches(alice, "bob")
    principal = directory.principal("alice", via=PrincipalVia.session)
    assert principal == Principal("alice", False, PrincipalVia.session)
    assert directory.principal("removed", via=PrincipalVia.session) is None
    # The credential digest covers the hash and the TOTP secret.
    assert directory.credential_digest("root") == credential_digest(
        password_hash(USERS["root"].password), TOTP_SECRET
    )
    assert directory.credential_digest("removed") is None
    digest = directory.credential_digest("root")
    assert directory.credential_matches("root", digest)
    assert not directory.credential_matches("root", b"stale")
    assert not directory.credential_matches("root", None)
    assert directory.changed_credentials({"root": digest, "alice": b"stale"}) == {
        "alice": directory.credential_digest("alice"),
        "bob": directory.credential_digest("bob"),
    }
    assert directory.has_totp("root") and not directory.has_totp("alice")
    assert not directory.has_totp("removed")
    # Every hash uses the same parameters: no warning, and the dummy uses them.
    assert directory.startup_warnings() == []
    assert (directory.hasher.time_cost, directory.hasher.memory_cost) == (1, 1024)


def test_admins_without_totp_are_refused_on_https_only() -> None:
    http = UserDirectory(_users_settings())
    root = http.get("root")
    assert root is not None and not http.admin_login_refused(root)
    https = UserDirectory(_users_settings(base_url="https://copycast.example"))
    root = https.get("root")
    alice = https.get("alice")
    assert root is not None and alice is not None
    assert https.admin_login_refused(root) and not https.admin_login_refused(alice)
    assert any("auth.admin_without_totp username=root" in w for w in https.startup_warnings())
    with_totp = UserDirectory(
        get_settings(
            base_url="https://copycast.example", auth=users_auth(totp={"root": TOTP_SECRET})
        )
    )
    root = with_totp.get("root")
    assert root is not None and not with_totp.admin_login_refused(root)


def test_the_dummy_verify_uses_the_slowest_parameters() -> None:
    slow = PasswordHasher(time_cost=2, memory_cost=2048, parallelism=2)
    settings = get_settings(
        base_url="http://testserver",
        auth={
            "users": [
                user_entry(USERS["root"]),
                user_entry(USERS["alice"], password_hash=slow.hash(USERS["alice"].password)),
            ]
        },
    )
    directory = UserDirectory(settings)
    hasher = directory.hasher
    assert (hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (2, 2048, 2)
    assert any("auth.hash_parameters_differ" in w for w in directory.startup_warnings())


def test_password_hasher_verifies_and_never_raises() -> None:
    phc = TEST_HASHER.hash("secret")
    assert TEST_HASHER.verify(phc, "secret")
    assert not TEST_HASHER.verify(phc, "wrong")
    assert not TEST_HASHER.verify("not a hash", "secret")
    TEST_HASHER.dummy_verify("anything")  # returns nothing, raises nothing
    assert hash_parameters(phc) == (1, 1024, 1)
    assert hash_parameters("garbage") is None
    defaults = PasswordHasher.slowest(["garbage"])
    assert defaults.memory_cost == 65536


def test_legacy_mode_never_stores_a_fast_digest_of_the_operator_password() -> None:
    """Regression: the fingerprint is a salted argon2 hash, not SHA-256 of the password."""
    password = "longenoughpw"
    directory = UserDirectory(_settings(password=password))
    digest = directory.credential_digest("copycast")
    assert digest is not None
    assert digest != credential_digest(password, None)
    assert digest != hashlib.sha256(f"{password}\n".encode()).digest()
    phc = digest.decode("ascii")
    assert phc.startswith("$argon2id$") and PasswordHasher().verify(phc, password)
    assert password.encode() not in digest
    # A restart (a new directory, a new salt) still recognises it: sessions survive.
    again = UserDirectory(_settings(password=password))
    assert again.credential_matches("copycast", digest)
    assert again.changed_credentials({"copycast": digest}) == {}
    # A new password, or the 3.0-foundation SHA-256 digest, does not match.
    changed = UserDirectory(_settings(password="another-password"))
    assert not changed.credential_matches("copycast", digest)
    assert not again.credential_matches("copycast", credential_digest(password, None))
    assert set(changed.changed_credentials({"copycast": digest})) == {"copycast"}
    assert not again.credential_matches("nobody", digest)


def test_has_totp_never_hashes_the_legacy_password() -> None:
    """Regression: get_me asks has_totp on the event loop; it must not run argon2."""
    directory = UserDirectory(_settings(password="correct horse battery"))
    assert not directory.has_totp("copycast")
    assert "_legacy_hash" not in vars(directory)
    # sync_users warms it (in a thread), so no later users()/get() hashes on the loop.
    directory.changed_credentials({})
    assert "_legacy_hash" in vars(directory)
