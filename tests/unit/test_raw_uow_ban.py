"""The API and MCP adapters reach SYSTEM's powers only where the allowlists say.

A unit of work from the container's raw factory, ``container.services``,
``SystemServicesDep`` and ``get_system_services`` all act as SYSTEM: they see and may
change every User's Feeds. Everything else in ``adapters/api`` and ``adapters/mcp`` acts
as the request's Principal (``ServicesDep``, ``container.services_for``). This test
parses every module of both packages and fails on any use outside its allowlist, so a
new route cannot silently act as SYSTEM.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "copycast" / "adapters"
PACKAGES = ("api", "mcp")

MODULE = "<module>"
"""The enclosing "function" of a module-level statement (an import)."""

UOW_FACTORY_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("api/auth.py", "require_feed_access"),
        # The legacy operator pair claims a sign-in attempt (login_attempts) on /feeds.
        ("api/auth.py", "_operator_pair_opens"),
        ("api/deps.py", "refuse_during_rebuild"),
        ("api/health.py", "_worker_seen_at"),
        ("api/routes/public.py", "asset"),
        ("api/routes/public.py", "artwork"),
    }
)
"""Where ``….uow_factory`` may be read: (module, enclosing function)."""

SYSTEM_DEP_MODULES: frozenset[str] = frozenset({"api/deps.py", "api/routes/public.py"})
"""Modules that may use ``SystemServicesDep``/``get_system_services`` anywhere."""

SYSTEM_DEP_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("api/routes/uploads.py", MODULE),
        ("api/routes/uploads.py", "put_upload"),
        # A login has no Principal yet; a logout needs none (it only drops the cookie's row).
        ("api/routes/auth.py", MODULE),
        ("api/routes/auth.py", "login"),
        ("api/routes/auth.py", "logout"),
        # The /media play route counts a download through SYSTEM's record_download only
        # (spec §1.13): as a parameter of the handler, named like its operation id, or
        # through a dependency function called record_download. play_item_asset and any
        # other handler act as the Principal alone.
        ("api/routes/play.py", MODULE),
        ("api/routes/play.py", "play_item_media"),
        ("api/routes/play.py", "record_download"),
    }
)
"""Where else they may be used: (module, enclosing function)."""

CONTAINER_SERVICES_ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("api/deps.py", "get_system_services"),
        # authenticate_session, authenticate_api_key: the middleware, and an open event
        # stream checking its session again.
        ("api/principal.py", "resolve_principal"),
        ("api/app.py", "lifespan"),  # the startup steps
        ("mcp/auth.py", "verify_token"),  # ApiKeyVerifier: authenticate_api_key
    }
)
"""Where ``container.services`` (or ``self.container``/``self._container``) may be read."""

CONTAINER_NAMES = frozenset({"container", "_container"})
SYSTEM_DEPS = frozenset({"SystemServicesDep", "get_system_services"})


@dataclass(frozen=True, slots=True)
class Use:
    module: str
    function: str
    what: str
    line: int

    def __str__(self) -> str:
        return f"{self.module}:{self.line} {self.what} in {self.function}"


def _modules() -> Iterator[tuple[str, ast.Module]]:
    for package in PACKAGES:
        for path in sorted((SRC / package).rglob("*.py")):
            relative = path.relative_to(SRC).as_posix()
            yield relative, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_container(node: ast.expr) -> bool:
    """``container``, ``self.container``, ``self._container`` (any ``….container``)."""
    if isinstance(node, ast.Name):
        return node.id in CONTAINER_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in CONTAINER_NAMES
    return False


class _Finder(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.uses: list[Use] = []

    @property
    def function(self) -> str:
        return self.stack[-1] if self.stack else MODULE

    def _record(self, what: str, node: ast.AST) -> None:
        self.uses.append(Use(self.module, self.function, what, getattr(node, "lineno", 0)))

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "uow_factory":
            self._record("uow_factory", node)
        elif node.attr == "services" and _is_container(node.value):
            self._record("container.services", node)
        elif node.attr in SYSTEM_DEPS:
            self._record(node.attr, node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in SYSTEM_DEPS:
            self._record(node.id, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in SYSTEM_DEPS:
                self._record(alias.name, node)


def _uses() -> list[Use]:
    uses: list[Use] = []
    for module, tree in _modules():
        finder = _Finder(module)
        finder.visit(tree)
        uses.extend(finder.uses)
    return uses


def _allowed(use: Use) -> bool:
    key = (use.module, use.function)
    if use.what == "uow_factory":
        return key in UOW_FACTORY_ALLOWED
    if use.what == "container.services":
        return key in CONTAINER_SERVICES_ALLOWED
    return use.module in SYSTEM_DEP_MODULES or key in SYSTEM_DEP_ALLOWED


def test_system_powers_stay_inside_their_allowlists() -> None:
    refused = [str(use) for use in _uses() if not _allowed(use)]
    assert not refused, "SYSTEM reached outside the allowlist:\n" + "\n".join(refused)


def test_the_allowlists_are_still_needed() -> None:
    """Every allowlisted function still exists and still uses what it is allowed (a stale
    entry would let a new function of the same name through unnoticed)."""
    seen = {(use.module, use.function) for use in _uses()}
    stale = sorted(
        entry for entry in UOW_FACTORY_ALLOWED | CONTAINER_SERVICES_ALLOWED if entry not in seen
    )
    assert not stale, f"allowlist entries no longer used: {stale}"


@pytest.mark.parametrize(
    ("source", "what"),
    [
        (
            "async def list_x(container):\n    async with container.uow_factory() as uow: ...",
            "uow_factory",
        ),
        ("def f(container):\n    return container.services", "container.services"),
        (
            "class A:\n    def f(self):\n        return self._container.services",
            "container.services",
        ),
        ("from copycast.adapters.api.deps import SystemServicesDep", "SystemServicesDep"),
        ("async def route(services: SystemServicesDep): ...", "SystemServicesDep"),
        ("def g():\n    return deps.get_system_services", "get_system_services"),
    ],
)
def test_the_finder_sees_every_kind_of_use(source: str, what: str) -> None:
    finder = _Finder("api/routes/new.py")
    finder.visit(ast.parse(source))
    assert [use.what for use in finder.uses] == [what]
    assert not any(_allowed(use) for use in finder.uses)


def test_the_finder_ignores_principal_services() -> None:
    finder = _Finder("api/routes/new.py")
    finder.visit(
        ast.parse(
            "async def route(services: ServicesDep, container: ContainerDep):\n"
            "    return container.services_for(principal), container.users, services.ctx"
        )
    )
    assert finder.uses == []


@pytest.mark.parametrize(
    ("function", "allowed"),
    [("play_item_media", True), ("record_download", True), ("play_item_asset", False)],
)
def test_the_media_play_route_may_take_system_services(function: str, allowed: bool) -> None:
    """D's /media handler declares SYSTEM services as a route parameter (to count a
    download); the asset handler may not."""
    finder = _Finder("api/routes/play.py")
    finder.visit(
        ast.parse(f"async def {function}(services: ServicesDep, system: SystemServicesDep): ...")
    )
    assert [use.what for use in finder.uses] == ["SystemServicesDep"]
    assert all(_allowed(use) is allowed for use in finder.uses)
