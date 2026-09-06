"""Composition root.

:func:`build_container` wires the engine (yt-dlp or an injected fake), the
database engine and sessionmaker, the on-disk layout, the unit-of-work factory,
the gateways the application services talk to (Source probing and search, the
public URLs, new ORM rows) and the services themselves, for the api process,
the worker and the tests. Adapter collaborators are resolved lazily on first
access through importlib so a container can be built before every adapter
package is imported.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any

from copycast.application.models import FeedCredentials, PodcastSearchResult
from copycast.application.ports import CancelToken, Engine
from copycast.application.services.context import ServiceContext, SourceSnapshot
from copycast.domain.enums import (
    ArchiveState,
    AssetFormat,
    AssetKind,
    AssetProvenance,
    AssetState,
    FeedKind,
    SourceKind,
)
from copycast.domain.exceptions import EngineUnavailable
from copycast.settings import Settings, SettingsError, get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from copycast.adapters.sources.probe import ProbeCache, ProbeEntry
    from copycast.application.services import Services


def _load(module: str, attr: str) -> Any:
    return getattr(importlib.import_module(module), attr)


class SourceGatewayAdapter:
    """``application.services.context.SourceGateway`` over ``adapters.sources``.

    One probe cache per process (the plan's "probe cache" of the api lifespan)
    so ``create_mirror`` reuses the listing a ``probe_source`` call produced.
    """

    def __init__(self, settings: Settings, engine: Engine, layout: Any) -> None:
        self._settings = settings
        self._engine = engine
        self._layout = layout

    @cached_property
    def cache(self) -> ProbeCache:
        cache_cls = _load("copycast.adapters.sources.probe", "ProbeCache")
        cache: ProbeCache = cache_cls()
        return cache

    def probe(
        self, url: str, *, options: Mapping[str, Any], cancel: CancelToken
    ) -> list[SourceSnapshot]:
        probe = _load("copycast.adapters.sources.probe", "probe")
        result = probe(url, engine=self._engine, options=options, cache=self.cache, cancel=cancel)
        snapshots: list[SourceSnapshot] = []
        for candidate in result.candidates:
            entry = self.cache.get_by_token(candidate.candidate_token)
            if entry is not None:
                snapshots.append(self._snapshot(entry))
        return snapshots

    def cached(self, *, token: str | None, url: str | None) -> SourceSnapshot | None:
        entry = self.cache.get(token=token, url=url)
        return self._snapshot(entry) if entry is not None else None

    def search_podcasts(self, query: str, limit: int) -> list[PodcastSearchResult]:
        search = _load("copycast.adapters.sources.itunes_search", "search_podcasts")
        source_error = _load("copycast.adapters.sources.http", "SourceError")
        try:
            results: list[PodcastSearchResult] = search(query, limit)
        except source_error as exc:
            raise EngineUnavailable(f"podcast search is unavailable: {exc}") from exc
        return results

    def save_snapshot(self, feed_id: str, snapshot: SourceSnapshot) -> None:
        """``source/feed.xml`` (RSS, verbatim) or ``source/listing.json`` (yt-dlp raw listing)."""
        write_atomic = _load("copycast.adapters.storage.atomic", "write_atomic")
        write_json_atomic = _load("copycast.adapters.storage.atomic", "write_json_atomic")
        self._layout.ensure_feed_dirs(feed_id)
        if snapshot.body is not None:
            write_atomic(self._layout.source_xml_path(feed_id), snapshot.body)
        elif snapshot.listing.raw is not None:
            write_json_atomic(self._layout.source_listing_path(feed_id), snapshot.listing.raw)

    @staticmethod
    def _snapshot(entry: ProbeEntry) -> SourceSnapshot:
        feed = entry.feed
        channel_xml: str | None = None
        if feed is not None and feed.parsed is not None:
            to_xml = _load("copycast.adapters.sources.rss", "channel_xml")
            channel_xml = to_xml(feed.parsed)
        return SourceSnapshot(
            candidate=entry.candidate,
            listing=entry.listing,
            body=feed.body if feed is not None else None,
            etag=feed.etag if feed is not None else None,
            last_modified=feed.last_modified if feed is not None else None,
            channel_xml=channel_xml,
        )


class PublicUrlsAdapter:
    """``adapters.feeds.urls`` bound to ``base_url``; embeds feed pairs only while auth is on."""

    def __init__(self, base_url: str, *, auth_enabled: bool = False) -> None:
        self._base_url = base_url
        self._auth_enabled = auth_enabled

    def feed_url(
        self, feed_id: str, *, username: str | None = None, password: str | None = None
    ) -> str:
        compose = _load("copycast.adapters.feeds.urls", "feed_url")
        if not self._auth_enabled:
            username = password = None
        url: str = compose(self._base_url, feed_id, username=username, password=password)
        return url

    def feed_credentials(self, username: str, password: str) -> FeedCredentials | None:
        if not self._auth_enabled:
            return None
        return FeedCredentials(username=username, password=password)

    def media_url(self, feed_id: str, item_id: str, ext: str) -> str:
        compose = _load("copycast.adapters.feeds.urls", "media_url")
        url: str = compose(self._base_url, feed_id, item_id, ext)
        return url

    def asset_url(self, feed_id: str, local_path: str) -> str:
        compose = _load("copycast.adapters.feeds.urls", "asset_url")
        url: str = compose(self._base_url, feed_id, local_path)
        return url


class RowFactoryAdapter:
    """New ORM rows for the repositories that only accept instances (``feeds.add``)."""

    def feed(self, **values: Any) -> Any:
        feed_cls = _load("copycast.adapters.db.models", "Feed")
        return feed_cls(**values)


@dataclass(frozen=True, slots=True)
class FeedHead:
    """What the public feed route needs before deciding whether to render."""

    id: str
    kind: FeedKind
    revision: int
    follow: bool
    paused: bool


@dataclass(frozen=True, slots=True)
class RenderedFeedDocument:
    """A rendered Mirror or Inbox Feed (``adapters.feeds.render.RenderedFeed`` re-shaped)."""

    body: bytes
    last_modified: datetime
    revision: int
    content_type: str


class FeedRendererAdapter:
    """``adapters.feeds.render`` over the repositories: builds the view models from rows.

    The API adapter may not import ``adapters.feeds`` or ``adapters.db``, so
    the composition root turns the rows of one feed into the renderer's
    ``FeedView``/``ItemView``/``AssetView`` here.
    """

    def __init__(self, container: Container) -> None:
        self._container = container

    async def head(self, feed_id: str) -> FeedHead | None:
        async with self._container.uow_factory() as uow:
            feed = await uow.feeds.get(feed_id)
            if feed is None:
                return None
            return FeedHead(
                id=feed.id,
                kind=FeedKind(feed.kind),
                revision=int(feed.revision),
                follow=bool(feed.follow),
                paused=bool(feed.paused),
            )

    async def render(self, feed_id: str) -> RenderedFeedDocument | None:
        """Render the feed from Postgres only; ``None`` for an unknown feed."""
        render_module = importlib.import_module("copycast.adapters.feeds.render")
        async with self._container.uow_factory() as uow:
            feed = await uow.feeds.get(feed_id)
            if feed is None:
                return None
            items = await uow.catalog.for_render(feed_id)
            assets = await uow.assets.for_feed(feed_id)
            by_item: dict[str | None, list[Any]] = {}
            for asset in assets:
                by_item.setdefault(asset.item_id, []).append(self._asset_view(render_module, asset))
            feed_view = render_module.FeedView(
                id=feed.id,
                kind=FeedKind(feed.kind),
                title=feed.title,
                revision=int(feed.revision),
                description=feed.description,
                author=feed.author,
                artwork_url=feed.artwork_url,
                language=feed.language,
                link=feed.source_url if feed.is_mirror else self._container.settings.base_url,
                source_kind=SourceKind(feed.source_kind) if feed.source_kind else None,
                source_channel_xml=feed.source_channel_xml,
                last_modified=feed.updated_at,
                assets=tuple(by_item.get(None, [])),
            )
            item_views = [
                render_module.ItemView(
                    id=item.id,
                    ordinal=item.ordinal,
                    title=item.title,
                    archive_state=ArchiveState(item.archive_state),
                    listed=bool(item.listed),
                    source_number=item.source_number,
                    source_season=item.source_season,
                    description=item.description,
                    author=item.author,
                    published_at=item.published_at,
                    first_seen_at=item.first_seen_at,
                    duration_seconds=item.duration_seconds,
                    source_url=item.source_url,
                    artwork_url=item.artwork_url,
                    media_ext=item.media_ext,
                    media_mime=item.media_mime,
                    media_bytes=item.media_bytes,
                    source_item_xml=item.source_item_xml,
                    assets=tuple(by_item.get(item.id, [])),
                )
                for item in items
            ]
        rendered = render_module.render_feed(
            feed_view, item_views, base_url=self._container.settings.base_url
        )
        return RenderedFeedDocument(
            body=bytes(rendered.body),
            last_modified=rendered.last_modified,
            revision=int(rendered.revision),
            content_type=str(rendered.content_type),
        )

    @staticmethod
    def _asset_view(render_module: Any, asset: Any) -> Any:
        return render_module.AssetView(
            kind=AssetKind(asset.kind),
            local_path=asset.local_path,
            state=AssetState(asset.state),
            provenance=AssetProvenance(asset.provenance),
            language=asset.language,
            format=AssetFormat(asset.format) if asset.format else None,
            mime=asset.mime,
            size_bytes=asset.size_bytes,
        )


class Container:
    """Runtime dependencies of one process.

    Attributes:
        settings: the effective :class:`Settings`.
        engine: the Engine port (``adapters.engine.build_engine`` or an injected fake).
        db_engine: the SQLAlchemy ``AsyncEngine`` (``adapters.db.session.create_db_engine``).
        sessionmaker: ``async_sessionmaker[AsyncSession]`` bound to ``db_engine``.
        layout: the data directory layout (``adapters.storage.layout.Layout``).
        uow_factory: ``adapters.db.uow.UnitOfWorkFactory(sessionmaker, layout)``;
            ``async with container.uow_factory() as uow`` opens one transaction.
        sources: the Source gateway (probe cache, iTunes search, Source snapshots).
        urls: the public URL composer bound to ``base_url``.
        rows: the ORM row factory.
        renderer: the public feed renderer (``adapters.feeds.render`` over the rows).
        services: the application services (``application.services.build_services``).

    Every collaborator is created on first access and memoized; plain
    properties (not ``cached_property``) so the class satisfies the
    ``ServiceContext`` protocol statically.
    """

    def __init__(self, settings: Settings, engine: Engine | None = None) -> None:
        self.settings = settings
        self._memo: dict[str, Any] = {}
        if engine is not None:
            self._memo["engine"] = engine

    def _cached(self, name: str, build: Callable[[], Any]) -> Any:
        if name not in self._memo:
            self._memo[name] = build()
        return self._memo[name]

    @property
    def engine(self) -> Engine:
        engine: Engine = self._cached(
            "engine", lambda: _load("copycast.adapters.engine", "build_engine")(self.settings)
        )
        return engine

    @property
    def db_engine(self) -> AsyncEngine:
        db_engine: AsyncEngine = self._cached(
            "db_engine",
            lambda: _load("copycast.adapters.db.session", "create_db_engine")(self.settings),
        )
        return db_engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        maker: async_sessionmaker[AsyncSession] = self._cached(
            "sessionmaker",
            lambda: _load("copycast.adapters.db.session", "create_sessionmaker")(self.db_engine),
        )
        return maker

    @property
    def layout(self) -> Any:
        return self._cached(
            "layout",
            lambda: _load("copycast.adapters.storage.layout", "Layout")(self.settings.data_dir),
        )

    @property
    def uow_factory(self) -> Any:
        return self._cached(
            "uow_factory",
            lambda: _load("copycast.adapters.db.uow", "UnitOfWorkFactory")(
                self.sessionmaker, self.layout
            ),
        )

    @property
    def sources(self) -> SourceGatewayAdapter:
        gateway: SourceGatewayAdapter = self._cached(
            "sources", lambda: SourceGatewayAdapter(self.settings, self.engine, self.layout)
        )
        return gateway

    @property
    def urls(self) -> PublicUrlsAdapter:
        urls: PublicUrlsAdapter = self._cached(
            "urls",
            lambda: PublicUrlsAdapter(
                self.settings.base_url, auth_enabled=self.settings.auth.enabled
            ),
        )
        return urls

    @property
    def rows(self) -> RowFactoryAdapter:
        rows: RowFactoryAdapter = self._cached("rows", RowFactoryAdapter)
        return rows

    @property
    def renderer(self) -> FeedRendererAdapter:
        renderer: FeedRendererAdapter = self._cached("renderer", lambda: FeedRendererAdapter(self))
        return renderer

    @property
    def expected_layout_version(self) -> str:
        version: str = _load("copycast.adapters.storage.layout", "LAYOUT_VERSION")
        return version

    def ensure_layout(self) -> str:
        """Write ``LAYOUT_VERSION`` on first run or refuse a mismatch (``SettingsError``)."""
        version: str = self.layout.ensure_version()
        return version

    async def prepare_schema(self) -> None:
        """The ``COPYCAST_AUTO_MIGRATE`` policy; a stale schema becomes a ``SettingsError``."""
        prepare = _load("copycast.adapters.db.migrate", "prepare_schema")
        not_current = _load("copycast.adapters.db.migrate", "SchemaNotCurrent")
        try:
            await prepare(self.db_engine, auto_migrate=self.settings.auto_migrate)
        except not_current as exc:
            raise SettingsError(str(exc)) from exc

    async def schema_is_current(self) -> bool:
        current: bool = await _load("copycast.adapters.db.migrate", "schema_is_current")(
            self.db_engine
        )
        return current

    def close_http_client(self) -> None:
        """Close the shared outbound httpx client (``adapters.sources.http``)."""
        _load("copycast.adapters.sources.http", "close_client")()

    @property
    def services(self) -> Services:
        services: Services = self._cached(
            "services", lambda: _load("copycast.application.services", "build_services")(self)
        )
        return services

    async def aclose(self) -> None:
        """Close the unit-of-work factory and dispose the database engine if ever created."""
        factory = self._memo.pop("uow_factory", None)
        if factory is not None:
            await factory.aclose()
        db_engine = self._memo.pop("db_engine", None)
        self._memo.pop("sessionmaker", None)
        if db_engine is not None:
            await db_engine.dispose()


def build_container(settings: Settings, engine: Engine | None = None) -> Container:
    return Container(settings, engine)


def as_service_context(container: Container) -> ServiceContext:
    """The container as the services see it (a static check that it fits the Protocol)."""
    return container


def create_asgi_app(settings: Settings | None = None) -> FastAPI:
    """uvicorn factory (``copycast.app:create_asgi_app``): settings, container, FastAPI app."""
    settings = settings or get_settings()
    container = build_container(settings)
    create_app = _load("copycast.adapters.api.app", "create_app")
    app: FastAPI = create_app(settings, container)
    return app
