"""Command line entry point: ``copycast api | worker | migrate | rebuild | config | openapi``.

Command implementations are imported lazily so the CLI loads (and ``config``
and ``--version`` work) even when an adapter package is absent.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import sys
from typing import Any

import click

from copycast.logging import configure_logging
from copycast.settings import Settings, SettingsError, get_settings
from copycast.version import APP_VERSION

EXIT_CONFIG = 2


def engine_version_line() -> str:
    """The engine version for ``--version``: from the engine adapter when present."""
    try:
        module = importlib.import_module("copycast.adapters.engine")
        info = module.engine_info()
        return f"{info.name} {info.version} ({info.channel})"
    except Exception:  # the adapter may not exist yet or may be broken
        try:
            return f"yt-dlp {importlib.metadata.version('yt-dlp')}"
        except importlib.metadata.PackageNotFoundError:
            return "yt-dlp unavailable"


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(f"copycast {APP_VERSION}")
    click.echo(f"engine {engine_version_line()}")
    ctx.exit()


def _load_settings() -> Settings:
    try:
        return get_settings()
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)


def _lazy(module: str, attr: str) -> Any:
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        click.echo(f"copycast: {module}.{attr} is not available: {exc}", err=True)
        sys.exit(1)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--version",
    is_flag=True,
    callback=_print_version,
    expose_value=False,
    is_eager=True,
    help="Print the application and engine versions and exit.",
)
def main() -> None:
    """Copycast: self-hosted podcast mirroring and archiving."""


@main.command()
@click.option("--reload", is_flag=True, help="Reload on source changes (development only).")
def api(reload: bool) -> None:
    """Run the HTTP API, MCP server and web UI."""
    settings = _load_settings()
    configure_logging(settings, process="api")
    try:
        settings.check_data_dir()
        _api_preflight(settings)
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    uvicorn = importlib.import_module("uvicorn")
    uvicorn.run(
        "copycast.app:create_asgi_app",
        factory=True,
        host=settings.bind,
        port=settings.port,
        reload=reload,
        log_config=None,
        access_log=True,
        timeout_graceful_shutdown=25,
    )


def _api_preflight(settings: Settings) -> None:
    """Layout version and schema policy before uvicorn starts (exit 2 on failure)."""
    from copycast.app import build_container

    preflight = _lazy("copycast.adapters.api.app", "preflight")
    container = build_container(settings)

    async def _run() -> None:
        try:
            await preflight(container)
        finally:
            await container.aclose()

    asyncio.run(_run())


@main.command()
def worker() -> None:
    """Run the job worker (refresh, archive, expand, prune, rebuild)."""
    settings = _load_settings()
    configure_logging(settings, process="worker")
    try:
        settings.check_data_dir()
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    run = _lazy("copycast.worker.main", "run")
    sys.exit(int(run(settings) or 0))


@main.command()
def migrate() -> None:
    """Apply database migrations to head."""
    settings = _load_settings()
    configure_logging(settings, process="cli")
    from copycast.app import build_container

    ensure_schema = _lazy("copycast.adapters.db.migrate", "ensure_schema")
    container = build_container(settings)

    async def _run() -> None:
        try:
            await ensure_schema(container.db_engine)
        finally:
            await container.aclose()

    asyncio.run(_run())
    click.echo("schema is at head")


@main.command()
@click.option("--yes", is_flag=True, help="Also delete database rows absent from disk.")
def rebuild(yes: bool) -> None:
    """Reconstruct the database from the data directory."""
    settings = _load_settings()
    configure_logging(settings, process="cli")
    try:
        settings.check_data_dir()
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    run_rebuild = _lazy("copycast.adapters.storage.rebuild", "rebuild")
    result = run_rebuild(settings, yes=yes)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    if result is not None:
        dump = getattr(result, "model_dump", None)
        payload = dump(mode="json") if callable(dump) else result
        click.echo(json.dumps(payload, indent=2, default=str))


@main.command()
def config() -> None:
    """Print the effective configuration (secrets redacted)."""
    settings = _load_settings()
    click.echo(json.dumps(settings.redacted(), indent=2, sort_keys=True))


@main.command()
def openapi() -> None:
    """Print the OpenAPI document of the HTTP API as JSON."""
    settings = _load_settings()
    configure_logging(settings, process="cli", level="WARNING")
    from copycast.app import create_asgi_app

    app = create_asgi_app(settings)
    click.echo(json.dumps(app.openapi(), indent=2, sort_keys=False))


if __name__ == "__main__":
    main()
