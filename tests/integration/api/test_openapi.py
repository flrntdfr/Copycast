"""The OpenAPI document: equals the committed ``web/openapi.json``; capability annotations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from copycast.adapters.api.app import create_app
from copycast.app import build_container
from copycast.application.capabilities import CAPABILITY_NAMES, routed_capabilities
from copycast.settings import Settings
from tests.support.fake_engine import FakeEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENAPI_PATH = REPO_ROOT / "web" / "openapi.json"


@pytest.fixture
def document(settings: Settings) -> dict[str, Any]:
    app = create_app(settings, build_container(settings, engine=FakeEngine()))
    return app.openapi()


def operations(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, item in document["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict)
    ]


def test_openapi_equals_committed_file(document: dict[str, Any]) -> None:
    assert OPENAPI_PATH.is_file(), "run `make openapi` (uv run copycast openapi > web/openapi.json)"
    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert document == committed, "web/openapi.json is stale: run `make openapi`"


def test_every_routed_capability_has_exactly_one_route(document: dict[str, Any]) -> None:
    seen: dict[str, list[str]] = {}
    for method, path, operation in operations(document):
        capability = operation.get("x-capability")
        if capability is None:
            continue
        assert capability in CAPABILITY_NAMES, (method, path, capability)
        assert operation["operationId"] == capability, (method, path)
        seen.setdefault(capability, []).append(f"{method} {path}")
    duplicates = {name: routes for name, routes in seen.items() if len(routes) != 1}
    assert not duplicates, duplicates
    assert set(seen) == routed_capabilities()


def test_operation_ids_are_unique(document: dict[str, Any]) -> None:
    ids = [operation["operationId"] for _, _, operation in operations(document)]
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def test_problem_responses_are_documented_as_problem_json(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    assert "Problem" in schemas
    get_feed = document["paths"]["/api/feeds/{feed_id}"]["get"]
    not_found = get_feed["responses"]["404"]
    assert list(not_found["content"]) == ["application/problem+json"]
    assert not_found["content"]["application/problem+json"]["schema"]["$ref"].endswith("/Problem")
    ok = get_feed["responses"]["200"]["content"]
    assert "application/json" in ok


def test_public_and_health_routes_carry_no_capability(document: dict[str, Any]) -> None:
    for method, path, operation in operations(document):
        if path.startswith(("/feeds/", "/healthz/")):
            assert "x-capability" not in operation, (method, path)
    assert "/api/events" in document["paths"]
    assert (
        "text/event-stream"
        in document["paths"]["/api/events"]["get"]["responses"]["200"]["content"]
    )
