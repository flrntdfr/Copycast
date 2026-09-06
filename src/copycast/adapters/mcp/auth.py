"""API keys on the MCP mount: a fastmcp token verifier over the key service, plus scope checks.

While authentication is on, every request to ``/mcp`` must carry
``Authorization: Bearer cck_...``; the verifier turns a valid key into an
``AccessToken`` whose scopes are those the key's ``KeyScope`` grants. Tools
then call :func:`check_scope` before doing anything (``guarded`` in
``adapters.mcp.tools`` wires it), so a ``read`` key cannot create a Mirror
and a ``write`` key cannot delete one. With authentication off there is no
token and every check passes.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from copycast.adapters.mcp.container import ServicesProvider
from copycast.domain.credentials import scopes_granted
from copycast.domain.enums import KeyScope

SCOPE_CLAIM = "copycast_scope"
KEY_ID_CLAIM = "copycast_key_id"


class ApiKeyVerifier(TokenVerifier):
    """``verify_token`` looks the bearer secret up through ``authenticate_api_key``."""

    def __init__(self, container: ServicesProvider) -> None:
        super().__init__()
        self._container = container

    async def verify_token(self, token: str) -> AccessToken | None:
        key = await self._container.services.authenticate_api_key(token)
        if key is None:
            return None
        return AccessToken(
            token=token,
            client_id=key.name,
            scopes=scopes_granted(key.scope),
            claims={SCOPE_CLAIM: key.scope.value, KEY_ID_CLAIM: str(key.id)},
        )


def current_scope() -> KeyScope | None:
    """The scope of the key behind the current call; ``None`` when no key is involved."""
    token = get_access_token()
    if token is None:
        return None
    value = token.claims.get(SCOPE_CLAIM)
    return KeyScope(value) if isinstance(value, str) else None


def check_scope(required: KeyScope, tool: str) -> None:
    """Raise a ``ToolError`` the agent can act on when the key's scope is too narrow."""
    token = get_access_token()
    if token is None:
        return
    if required.value in token.scopes:
        return
    granted = token.claims.get(SCOPE_CLAIM, "unknown")
    raise ToolError(
        f"{tool} needs an API key with scope '{required.value}'; "
        f"this key ({token.client_id}) has scope '{granted}' [forbidden]"
    )


__all__ = ["KEY_ID_CLAIM", "SCOPE_CLAIM", "ApiKeyVerifier", "check_scope", "current_scope"]
