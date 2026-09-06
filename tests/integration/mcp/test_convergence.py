"""The convergence rule: capability -> exactly one route -> exactly one tool, same model.

Every registered capability (minus the internal ones, plus ``subscribe_events``)
has exactly one route whose ``x-capability`` and ``operation_id`` equal its
name; every non-exempt capability has exactly one tool whose ``meta.capability``
equals its name and whose input model is the identical Pydantic class; no
route or tool names an unregistered capability. Runs without a database.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel

from copycast.adapters.api.app import create_app
from copycast.app import Container, build_container
from copycast.application import services as _services  # noqa: F401 - registers capabilities
from copycast.application.capabilities import (
    CAPABILITIES,
    CAPABILITY_NAMES,
    DESTRUCTIVE,
    INTERNAL,
    TOOL_EXEMPT,
    routed_capabilities,
    tool_capabilities,
)
from copycast.settings import Settings
from tests.support.fake_engine import FakeEngine


@pytest.fixture
def container(settings: Settings) -> Container:
    return build_container(settings, engine=FakeEngine())


@pytest.fixture
def app(settings: Settings, container: Container) -> FastAPI:
    return create_app(settings, container)


def api_routes(app: FastAPI) -> list[APIRoute]:
    """Every APIRoute, through FastAPI's included-router wrappers."""
    return [
        context.original_route
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
    ]


def route_capability(route: APIRoute) -> str | None:
    extra = route.openapi_extra or {}
    value = extra.get("x-capability")
    return value if isinstance(value, str) else None


async def tools_of(app: FastAPI) -> list[Any]:
    return list(await app.state.mcp.list_tools(run_middleware=False))


def request_models(fn: Any) -> list[type[BaseModel]]:
    """Pydantic request models among a tool function's parameters (through ``functools.wraps``)."""
    models: list[type[BaseModel]] = []
    for parameter in inspect.signature(fn).parameters.values():
        annotation = parameter.annotation
        if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
            models.append(annotation)
    return models


def test_every_capability_has_a_service() -> None:
    assert set(CAPABILITIES) == CAPABILITY_NAMES - {"subscribe_events"}
    assert INTERNAL <= CAPABILITY_NAMES and TOOL_EXEMPT <= CAPABILITY_NAMES
    assert tool_capabilities() >= DESTRUCTIVE


def test_every_routed_capability_has_exactly_one_route(app: FastAPI) -> None:
    by_capability: dict[str, list[APIRoute]] = {}
    for route in api_routes(app):
        name = route_capability(route)
        if name is None:
            continue
        assert name in CAPABILITY_NAMES, f"{route.path} names unregistered capability {name!r}"
        assert route.operation_id == name, (route.path, route.operation_id)
        by_capability.setdefault(name, []).append(route)
    wrong = {name: [r.path for r in rs] for name, rs in by_capability.items() if len(rs) != 1}
    assert not wrong, wrong
    assert set(by_capability) == routed_capabilities()


def test_routes_use_the_capability_request_model(app: FastAPI) -> None:
    for route in api_routes(app):
        name = route_capability(route)
        if name is None or name == "subscribe_events":
            continue
        expected = CAPABILITIES[name].request_model
        annotations = [field.field_info.annotation for field in route.dependant.body_params]
        body_models = [
            annotation
            for annotation in annotations
            if inspect.isclass(annotation) and issubclass(annotation, BaseModel)
        ]
        if expected is None:
            assert body_models == [], (route.path, body_models)
        else:
            assert body_models == [expected], (route.path, body_models, expected)


async def test_every_tool_capability_has_exactly_one_tool(app: FastAPI) -> None:
    by_capability: dict[str, list[Any]] = {}
    for tool in await tools_of(app):
        meta = tool.meta or {}
        name = meta.get("capability")
        assert isinstance(name, str), f"tool {tool.name!r} carries no meta.capability"
        assert name in CAPABILITY_NAMES, (
            f"tool {tool.name!r} names unregistered capability {name!r}"
        )
        assert name not in INTERNAL and name not in TOOL_EXEMPT, (tool.name, name)
        by_capability.setdefault(name, []).append(tool)
    wrong = {name: [t.name for t in ts] for name, ts in by_capability.items() if len(ts) != 1}
    assert not wrong, wrong
    assert set(by_capability) == tool_capabilities()


async def test_tools_take_the_identical_request_model(app: FastAPI) -> None:
    for tool in await tools_of(app):
        name = (tool.meta or {})["capability"]
        expected = CAPABILITIES[name].request_model
        models = request_models(tool.fn)
        if expected is None:
            assert models == [], (tool.name, models)
            continue
        if models:
            assert models == [expected], (tool.name, models, expected)
            assert models[0] is expected
        else:
            # A one-field request model may be flattened into scalar arguments (probe_source).
            fields = set(expected.model_fields)
            params = set(inspect.signature(tool.fn).parameters)
            assert fields <= params, (tool.name, fields, params)
            assert len(fields) == 1, (tool.name, "only single-field models may be flattened")


async def test_destructive_tools_carry_the_hint(app: FastAPI) -> None:
    for tool in await tools_of(app):
        name = (tool.meta or {})["capability"]
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert bool(annotations.destructive_hint) == (name in DESTRUCTIVE), tool.name
