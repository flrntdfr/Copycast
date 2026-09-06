"""``/api/keys``: the MCP API keys an operator mints and revokes from the UI."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response

from copycast.adapters.api.deps import ServicesDep
from copycast.adapters.api.problems import problem_responses
from copycast.application.models import ApiKeyCreate, ApiKeyCreated, ApiKeyList

router = APIRouter(tags=["keys"])


@router.get(
    "/keys",
    operation_id="list_api_keys",
    openapi_extra={"x-capability": "list_api_keys"},
    response_model=ApiKeyList,
    summary="List API keys (never their secrets)",
)
async def list_api_keys(services: ServicesDep) -> ApiKeyList:
    return await services.list_api_keys()


@router.post(
    "/keys",
    operation_id="create_api_key",
    openapi_extra={"x-capability": "create_api_key"},
    response_model=ApiKeyCreated,
    status_code=201,
    summary="Mint an API key for MCP; the secret is returned exactly once",
)
async def create_api_key(services: ServicesDep, body: ApiKeyCreate) -> ApiKeyCreated:
    return await services.create_api_key(body)


@router.delete(
    "/keys/{key_id}",
    operation_id="revoke_api_key",
    openapi_extra={"x-capability": "revoke_api_key"},
    status_code=204,
    responses=problem_responses(404),
    summary="Revoke an API key",
)
async def revoke_api_key(services: ServicesDep, key_id: uuid.UUID) -> Response:
    await services.revoke_api_key(key_id)
    return Response(status_code=204)


__all__ = ["router"]
