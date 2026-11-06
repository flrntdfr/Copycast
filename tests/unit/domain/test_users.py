"""Usernames, the legacy owner slug and Principals."""

from __future__ import annotations

import uuid

import pytest

from copycast.domain.enums import KeyScope, PrincipalVia
from copycast.domain.users import (
    FALLBACK_OWNER,
    Principal,
    is_valid_username,
    owner_slug,
)


@pytest.mark.parametrize("name", ["a", "florent", "emilia.k", "u_1", "a-b", "0", "a" * 32])
def test_valid_usernames(name: str) -> None:
    assert is_valid_username(name)


@pytest.mark.parametrize(
    "name", ["", "Florent", "-a", ".a", "_a", "a b", "a@b", "a/b", "a" * 33, "ü", "a\n"]
)
def test_invalid_usernames(name: str) -> None:
    assert not is_valid_username(name)


@pytest.mark.parametrize(
    ("login", "owner"),
    [
        ("copycast", "copycast"),
        ("Florent", "florent"),
        ("  Florent  ", "florent"),
        ("me@example.org", "me-example.org"),
        ("Jean Dupont", "jean-dupont"),
        ("--admin", "admin"),
        ("@@@", FALLBACK_OWNER),
        ("___", FALLBACK_OWNER),
        ("ü", FALLBACK_OWNER),
        ("x" * 40, "x" * 32),
        ("Émile", "mile"),
    ],
)
def test_owner_slug(login: str, owner: str) -> None:
    assert owner_slug(login) == owner
    assert is_valid_username(owner_slug(login))


def test_owner_slug_is_deterministic() -> None:
    assert owner_slug("Me@Example.org") == owner_slug("me@example.org")


def test_system_principal() -> None:
    system = Principal.SYSTEM
    assert system.username is None and system.admin and system.is_system
    assert system.via is PrincipalVia.system
    assert system.api_key_id is None and system.key_scope is None


def test_open_admin_and_key_principals() -> None:
    admin = Principal.open_admin("copycast")
    assert admin == Principal(username="copycast", admin=True, via=PrincipalVia.open)
    assert not admin.is_system
    key_id = uuid.uuid4()
    key = Principal(
        username="alice",
        admin=False,
        via=PrincipalVia.api_key,
        api_key_id=key_id,
        key_scope=KeyScope.read,
    )
    assert key.api_key_id == key_id and key.key_scope is KeyScope.read and not key.is_system
    with pytest.raises(AttributeError):
        key.admin = True  # type: ignore[misc]
