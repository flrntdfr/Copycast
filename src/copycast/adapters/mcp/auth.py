"""API keys on the MCP mount: a fastmcp token verifier over the key service, plus scope checks.

While authentication is on, every request to ``/mcp`` must carry
``Authorization: Bearer cck_...``; the verifier turns a valid key into an
``AccessToken`` whose scopes are those the key's ``KeyScope`` grants. Tools
then call :func:`check_scope` before doing anything (``guarded`` in
``adapters.mcp.tools`` wires it), so a ``read`` key cannot create a Mirror
and a ``write`` key cannot delete one. With authentication off there is no
token and every check passes. :func:`current_principal` says whom a call acts as:
the key's owner (claim ``copycast_owner``), never more than that User may see.
Argument-level checks (deleting policies, ``engine_options``, ``autoprune_days``)
are the services' (``application.services.keyscope``), as on ``/api``.
"""

from __future__ import annotations

import uuid

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from copycast.adapters.mcp.container import ServicesProvider
from copycast.domain.credentials import scopes_granted
from copycast.domain.enums import AuthMode, KeyScope, PrincipalVia
from copycast.domain.users import Principal

SCOPE_CLAIM = "copycast_scope"
KEY_ID_CLAIM = "copycast_key_id"
OWNER_CLAIM = "copycast_owner"


class ApiKeyVerifier(TokenVerifier):
    """``verify_token`` looks the bearer secret up through ``authenticate_api_key``."""

    def __init__(self, container: ServicesProvider) -> None:
        super().__init__()
        self._container = container

    async def verify_token(self, token: str) -> AccessToken | None:
        """The key's token, or None for an unknown key or one whose owner is not configured."""
        key = await self._container.services.authenticate_api_key(token)
        if key is None or not self._container.users.is_configured(key.owner):
            return None
        return AccessToken(
            token=token,
            client_id=key.name,
            scopes=scopes_granted(key.scope),
            claims={
                SCOPE_CLAIM: key.scope.value,
                KEY_ID_CLAIM: str(key.id),
                OWNER_CLAIM: key.owner,
            },
        )


def current_principal(container: ServicesProvider) -> Principal:
    """Whom the current MCP call acts as: the key's owner, or the open-mode Admin.

    Without a token outside open mode the call is refused (the verifier makes that
    unreachable, but the branch stays closed); a key whose owner is no longer
    configured is refused too.
    """
    users = container.users
    token = get_access_token()
    if token is None:
        principal = users.open_principal() if users.mode is AuthMode.open else None
        if principal is None:
            raise ToolError("authentication required [unauthorized]")
        return principal
    owner = token.claims.get(OWNER_CLAIM)
    scope = token.claims.get(SCOPE_CLAIM)
    key_id = token.claims.get(KEY_ID_CLAIM)
    principal = (
        users.principal(
            owner,
            via=PrincipalVia.api_key,
            api_key_id=_uuid(key_id),
            key_scope=KeyScope(scope) if isinstance(scope, str) else None,
        )
        if isinstance(owner, str)
        else None
    )
    if principal is None:
        raise ToolError("this API key's owner is not a configured User [unauthorized]")
    return principal


def _uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


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


__all__ = [
    "KEY_ID_CLAIM",
    "OWNER_CLAIM",
    "SCOPE_CLAIM",
    "ApiKeyVerifier",
    "check_scope",
    "current_principal",
    "current_scope",
]
