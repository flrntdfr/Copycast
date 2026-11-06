"""The UserDirectory in open and legacy mode (users mode arrives with the settings)."""

from __future__ import annotations

from typing import Any

from copycast.application.users import DirectoryUser, UserDirectory
from copycast.domain.credentials import credential_digest
from copycast.domain.enums import AuthMode, KeyScope, PrincipalVia
from copycast.domain.users import Principal
from copycast.settings import Settings, get_settings


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
    assert directory.credential_digest("florent") == credential_digest(
        "correct horse battery", None
    )
    assert directory.credential_digest("nobody") is None
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
