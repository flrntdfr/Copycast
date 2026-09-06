"""What the API needs from the composition root, described structurally.

``adapters.api`` imports ``application`` only, so the container
(``copycast.app.Container``) arrives typed against these Protocols; the
renderer and the startup seams it provides are duck-typed the same way.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from copycast.application.services import Services
from copycast.domain.enums import FeedKind
from copycast.settings import Settings


class FeedHeadLike(Protocol):
    """What the public feed route needs before deciding whether to render."""

    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> FeedKind: ...
    @property
    def revision(self) -> int: ...
    @property
    def follow(self) -> bool: ...
    @property
    def paused(self) -> bool: ...


class RenderedFeedLike(Protocol):
    @property
    def body(self) -> bytes: ...
    @property
    def last_modified(self) -> datetime: ...
    @property
    def revision(self) -> int: ...
    @property
    def content_type(self) -> str: ...


class FeedRendererLike(Protocol):
    async def head(self, feed_id: str) -> FeedHeadLike | None: ...
    async def render(self, feed_id: str) -> RenderedFeedLike | None: ...


class SourceGatewayLike(Protocol):
    @property
    def cache(self) -> Any: ...


class ApiContainer(Protocol):
    """``copycast.app.Container`` as the API sees it."""

    @property
    def settings(self) -> Settings: ...
    @property
    def services(self) -> Services: ...
    @property
    def uow_factory(self) -> Any: ...
    @property
    def layout(self) -> Any: ...
    @property
    def db_engine(self) -> AsyncEngine: ...
    @property
    def renderer(self) -> FeedRendererLike: ...
    @property
    def sources(self) -> SourceGatewayLike: ...
    @property
    def expected_layout_version(self) -> str: ...
    def ensure_layout(self) -> str: ...
    async def prepare_schema(self) -> None: ...
    async def schema_is_current(self) -> bool: ...
    def close_http_client(self) -> None: ...
    async def aclose(self) -> None: ...


__all__ = [
    "ApiContainer",
    "FeedHeadLike",
    "FeedRendererLike",
    "RenderedFeedLike",
    "SourceGatewayLike",
]
