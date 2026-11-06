"""Scope checks on MCP tools: no key passes, a key narrower than the tool's scope is refused."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

from copycast.adapters.mcp import auth
from copycast.adapters.mcp.tools import with_scope
from copycast.domain.credentials import scopes_granted
from copycast.domain.enums import AuthMode, KeyScope, PrincipalVia
from copycast.domain.users import Principal


def token(scope: KeyScope) -> AccessToken:
    return AccessToken(
        token="cck_x",
        client_id="laptop",
        scopes=scopes_granted(scope),
        claims={auth.SCOPE_CLAIM: scope.value, auth.KEY_ID_CLAIM: "k"},
    )


def test_no_token_means_auth_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    assert auth.current_scope() is None
    auth.check_scope(KeyScope.full, "delete_feed")  # no exception


@pytest.mark.parametrize(
    ("granted", "required", "ok"),
    [
        (KeyScope.read, KeyScope.read, True),
        (KeyScope.read, KeyScope.write, False),
        (KeyScope.write, KeyScope.full, False),
        (KeyScope.full, KeyScope.full, True),
    ],
)
def test_check_scope(
    monkeypatch: pytest.MonkeyPatch, granted: KeyScope, required: KeyScope, ok: bool
) -> None:
    monkeypatch.setattr(auth, "get_access_token", lambda: token(granted))
    assert auth.current_scope() is granted
    if ok:
        auth.check_scope(required, "tool")
        return
    with pytest.raises(ToolError, match=rf"tool needs an API key with scope '{required.value}'"):
        auth.check_scope(required, "tool")


async def test_with_scope_wraps_the_tool_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def body(feed_id: str, confirm: bool = False) -> dict[str, Any]:
        """Docstring survives."""
        calls.append((feed_id, confirm))
        return {"deleted": True}

    wrapped = with_scope(body, "delete_feed", KeyScope.full)
    assert wrapped.__doc__ == "Docstring survives." and wrapped.__name__ == "body"
    monkeypatch.setattr(auth, "get_access_token", lambda: token(KeyScope.write))
    with pytest.raises(ToolError, match=r"laptop.*has scope 'write'"):
        await wrapped("abc", confirm=True)
    assert calls == []
    monkeypatch.setattr(auth, "get_access_token", lambda: token(KeyScope.full))
    assert await wrapped("abc", confirm=True) == {"deleted": True}
    assert calls == [("abc", True)]


# --------------------------------------------------------------------------- current_principal


class _Directory:
    """The slice of ``UserDirectory`` ``current_principal`` reads."""

    def __init__(self, mode: AuthMode, configured: tuple[str, ...] = ("alice",)) -> None:
        self.mode = mode
        self._configured = configured

    def open_principal(self) -> Principal | None:
        return Principal.open_admin("copycast") if self.mode is AuthMode.open else None

    def principal(
        self,
        username: str,
        *,
        via: PrincipalVia,
        api_key_id: uuid.UUID | None = None,
        key_scope: KeyScope | None = None,
    ) -> Principal | None:
        if username not in self._configured:
            return None
        return Principal(
            username=username, admin=False, via=via, api_key_id=api_key_id, key_scope=key_scope
        )


class _Container:
    def __init__(self, users: _Directory) -> None:
        self.users = users


def owner_token(scope: KeyScope, owner: str, key_id: str) -> AccessToken:
    return AccessToken(
        token="cck_x",
        client_id="laptop",
        scopes=scopes_granted(scope),
        claims={auth.SCOPE_CLAIM: scope.value, auth.KEY_ID_CLAIM: key_id, auth.OWNER_CLAIM: owner},
    )


def test_no_token_acts_as_the_open_mode_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    principal = auth.current_principal(_Container(_Directory(AuthMode.open)))  # type: ignore[arg-type]
    assert principal.username == "copycast" and principal.admin
    assert principal.via is PrincipalVia.open


@pytest.mark.parametrize("mode", [AuthMode.legacy, AuthMode.users])
def test_no_token_outside_open_mode_is_refused(
    monkeypatch: pytest.MonkeyPatch, mode: AuthMode
) -> None:
    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    with pytest.raises(ToolError, match=r"\[unauthorized\]"):
        auth.current_principal(_Container(_Directory(mode)))  # type: ignore[arg-type]


def test_a_key_acts_as_its_owner_with_its_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    key_id = uuid.uuid4()
    monkeypatch.setattr(
        auth, "get_access_token", lambda: owner_token(KeyScope.write, "alice", str(key_id))
    )
    principal = auth.current_principal(_Container(_Directory(AuthMode.users)))  # type: ignore[arg-type]
    assert principal.username == "alice" and principal.via is PrincipalVia.api_key
    assert principal.key_scope is KeyScope.write and principal.api_key_id == key_id


def test_a_key_of_a_removed_owner_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth, "get_access_token", lambda: owner_token(KeyScope.full, "gone", "not-a-uuid")
    )
    with pytest.raises(ToolError, match=r"\[unauthorized\]"):
        auth.current_principal(_Container(_Directory(AuthMode.users)))  # type: ignore[arg-type]


async def test_with_scope_binds_the_principals_services(monkeypatch: pytest.MonkeyPatch) -> None:
    from copycast.adapters.mcp import tools

    seen: list[Principal] = []

    class _Scoped(_Container):
        def services_for(self, principal: Principal) -> Any:
            seen.append(principal)
            return f"services of {principal.username}"

    async def body() -> Any:
        return tools.services()

    container = _Scoped(_Directory(AuthMode.users))
    wrapped = with_scope(body, "list_feeds", KeyScope.read, container)  # type: ignore[arg-type]
    monkeypatch.setattr(auth, "get_access_token", lambda: owner_token(KeyScope.read, "alice", "k"))
    assert await wrapped() == "services of alice"
    assert seen[0].username == "alice" and seen[0].via is PrincipalVia.api_key
    # Outside a wrapped call there are no services to use.
    with pytest.raises(RuntimeError):
        tools.services()
