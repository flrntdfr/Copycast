"""Scope checks on MCP tools: no key passes, a key narrower than the tool's scope is refused."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

from copycast.adapters.mcp import auth
from copycast.adapters.mcp.tools import with_scope
from copycast.domain.credentials import scopes_granted
from copycast.domain.enums import KeyScope


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
