"""The services as one Principal sees them: the container with that Principal's Scope.

:class:`ScopedContext` delegates everything to the container but ``principal`` and
``uow_factory``: every unit of work it opens stamps the Principal's username on the
rows it creates and (once the repositories filter) shows only what the Principal may
see. ``services_for(base, principal)`` in ``application.services`` builds one per
request.
"""

from __future__ import annotations

from dataclasses import dataclass

from copycast.application.ports import Engine
from copycast.application.services.context import (
    LayoutPort,
    PublicUrls,
    RowFactory,
    Scope,
    ScratchPort,
    ServiceContext,
    SourceGateway,
    UnitOfWorkFactoryPort,
    UnitOfWorkPort,
)
from copycast.application.users import UserDirectory
from copycast.domain.users import Principal
from copycast.settings import Settings


@dataclass(frozen=True, slots=True)
class ScopedUnitOfWorkFactory:
    """The container's factory, opening units of work in ``scope`` unless told otherwise."""

    base: UnitOfWorkFactoryPort
    scope: Scope

    def __call__(self, scope: Scope | None = None) -> UnitOfWorkPort:
        return self.base(self.scope if scope is None else scope)

    async def flush_exports(self) -> None:
        await self.base.flush_exports()

    async def aclose(self) -> None:
        """The container owns the factory: closing a scoped view closes nothing."""


@dataclass(frozen=True, slots=True)
class ScopedContext:
    """A ``ServiceContext`` acting as ``principal``."""

    base: ServiceContext
    principal: Principal

    @property
    def uow_factory(self) -> UnitOfWorkFactoryPort:
        return ScopedUnitOfWorkFactory(self.base.uow_factory, Scope.for_principal(self.principal))

    @property
    def settings(self) -> Settings:
        return self.base.settings

    @property
    def users(self) -> UserDirectory:
        return self.base.users

    @property
    def engine(self) -> Engine:
        return self.base.engine

    @property
    def layout(self) -> LayoutPort:
        return self.base.layout

    @property
    def scratch(self) -> ScratchPort:
        return self.base.scratch

    @property
    def sources(self) -> SourceGateway:
        return self.base.sources

    @property
    def urls(self) -> PublicUrls:
        return self.base.urls

    @property
    def rows(self) -> RowFactory:
        return self.base.rows

    @property
    def expected_layout_version(self) -> str:
        return self.base.expected_layout_version

    async def schema_is_current(self) -> bool:
        return await self.base.schema_is_current()


def as_service_context(scoped: ScopedContext) -> ServiceContext:
    """A static check that :class:`ScopedContext` fits the Protocol."""
    return scoped


__all__ = ["ScopedContext", "ScopedUnitOfWorkFactory", "as_service_context"]
