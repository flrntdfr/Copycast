"""Command line entry point: ``copycast api | worker | migrate | rebuild | config | openapi``,
plus ``storage push``, the ``users`` credential helpers and ``users reassign``.

Command implementations are imported lazily so the CLI loads (and ``config``
and ``--version`` work) even when an adapter package is absent.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path
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
        settings.check_scratch_dir()
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
        # X-Forwarded-For/-Proto only from the proxies named here (None: uvicorn's default,
        # 127.0.0.1): the request log's client_ip and the request scheme come from them.
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        reload=reload,
        log_config=None,
        access_log=False,
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
        settings.check_scratch_dir()
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
            await ensure_schema(
                container.db_engine,
                owner_of_existing=settings.auth.effective_owner_of_existing,
            )
        finally:
            await container.aclose()

    asyncio.run(_run())
    click.echo("schema is at head")


@main.command()
@click.option("--yes", is_flag=True, help="Also delete database rows absent from the Object store.")
def rebuild(yes: bool) -> None:
    """Reconstruct the database from the Object store."""
    settings = _load_settings()
    configure_logging(settings, process="cli")
    try:
        settings.check_scratch_dir()
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    run_rebuild = _lazy("copycast.adapters.storage.rebuild", "rebuild")
    try:
        result = run_rebuild(settings, yes=yes)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    if result is not None:
        dump = getattr(result, "model_dump", None)
        payload = dump(mode="json") if callable(dump) else result
        click.echo(json.dumps(payload, indent=2, default=str))


@main.group()
def storage() -> None:
    """The Object store: copy a pre-2.0 data directory into it."""


@storage.command("push")
@click.option(
    "--from",
    "source",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="The layout-1 data directory to copy (left untouched).",
)
@click.option("--dry-run", is_flag=True, help="Count what would be uploaded; write nothing.")
def storage_push(source: Path, dry_run: bool) -> None:
    """Copy a data directory into the Object store, then mark the Object store as layout 2.

    Resumable: objects already present with the same size are skipped. Exits 1
    when any file failed (LAYOUT_VERSION is then not written; run it again).
    """
    settings = _load_settings()
    configure_logging(settings, process="cli")
    layout_cls = _lazy("copycast.adapters.storage.layout", "Layout")
    open_store = _lazy("copycast.adapters.storage.objects", "open_store")
    run_push = _lazy("copycast.adapters.storage.push", "push")
    try:
        report = run_push(layout_cls(open_store(settings.storage)), source, dry_run=dry_run)
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    click.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    if report.failures:
        sys.exit(1)


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


PASSWORD_MIN_LEN = 8


@main.group()
def users() -> None:
    """Credentials for ``[[auth.users]]`` (no database needed), and ``reassign``."""


@users.command("hash-password")
@click.option(
    "--stdin", "from_stdin", is_flag=True, help="Read the password from one line of stdin."
)
def users_hash_password(from_stdin: bool) -> None:
    """Print the argon2id hash of a password, for ``password_hash`` in copycast.toml.

    Prompts twice (hidden) unless ``--stdin``. Put the hash in the TOML file, never in
    an ``.env`` or compose file: it contains ``$`` signs.
    """
    from copycast.application.passwords import PasswordHasher

    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    if len(password) < PASSWORD_MIN_LEN:
        click.echo(f"copycast: a password needs at least {PASSWORD_MIN_LEN} characters", err=True)
        sys.exit(EXIT_CONFIG)
    click.echo(PasswordHasher().hash(password))


@users.command("totp")
@click.argument("username")
@click.option("--secret", default=None, help="Use this base32 secret instead of a new one.")
def users_totp(username: str, secret: str | None) -> None:
    """Print a TOTP secret for USERNAME, its otpauth:// URI and a QR code to scan.

    Put the secret in ``totp_secret``; an authenticator app scans the QR code.
    """
    import io

    import segno

    from copycast.domain.totp import decode_secret, generate_secret, provisioning_uri
    from copycast.domain.users import is_valid_username

    username = username.strip()
    if not is_valid_username(username):
        click.echo(
            "copycast: a username is 1-32 lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit",
            err=True,
        )
        sys.exit(EXIT_CONFIG)
    if secret is None:
        secret = generate_secret()
    else:
        secret = "".join(secret.split()).upper().rstrip("=")
        try:
            decode_secret(secret)
        except ValueError:
            secret = ""
        if len(secret) < 16:
            click.echo("copycast: --secret must be base32, at least 16 characters", err=True)
            sys.exit(EXIT_CONFIG)
    uri = provisioning_uri(secret, username)
    qr = io.StringIO()
    segno.make(uri, error="m").terminal(out=qr, compact=True)
    click.echo(f"secret: {secret}")
    click.echo(f"uri:    {uri}")
    click.echo(qr.getvalue().rstrip("\n"))
    click.echo(f'copycast.toml, under this user\'s [[auth.users]]: totp_secret = "{secret}"')


@users.command("reassign")
@click.argument("old")
@click.argument("new")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.option(
    "--move-keys",
    is_flag=True,
    help="Also give OLD's API keys to NEW: whoever holds them then acts as NEW.",
)
def users_reassign(old: str, new: str, yes: bool, move_keys: bool) -> None:
    """Give every Feed and job of OLD, and its Defaults and Cookie files, to NEW.

    For Feeds whose owner is no longer configured (a renamed user, a switch to users
    mode). NEW must be configured. Refused while the worker runs (stop it first), and when
    OLD and NEW mirror the same Source. Feed URLs, pairs and media keys do not change.

    OLD's API keys stay OLD's (they stop working while OLD is not configured). Pass
    --move-keys only when OLD and NEW are the same person: a moved key acts as NEW, with
    NEW's Admin powers if NEW is an Admin.
    """
    settings = _load_settings()
    configure_logging(settings, process="cli")
    old, new = old.strip(), new.strip()
    check_names = _lazy("copycast.adapters.db.reassign", "check_names")
    old_is_configured = _lazy("copycast.adapters.db.reassign", "old_is_configured")
    refused = _lazy("copycast.adapters.db.reassign", "ReassignRefused")
    run_reassign = _lazy("copycast.adapters.db.reassign", "reassign")
    try:
        check_names(settings, old, new)
    except refused as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(1)
    if old_is_configured(settings, old):
        click.echo(
            f"warning: {old} is still a configured User; they keep signing in, with no Feeds",
            err=True,
        )
    if move_keys:
        click.echo(
            f"warning: whoever holds {old}'s API keys will act as {new}, "
            f"with everything {new} may do",
            err=True,
        )
    if not yes:
        what = "Feeds, API keys, jobs" if move_keys else "Feeds, jobs"
        click.confirm(f"Give everything {old} owns ({what}, users/{old}/) to {new}?", abort=True)
    try:
        report = asyncio.run(run_reassign(settings, old, new, move_keys=move_keys))
    except SettingsError as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(EXIT_CONFIG)
    except refused as exc:
        click.echo(f"copycast: {exc}", err=True)
        sys.exit(1)
    if report.demoted_default is not None:
        click.echo(
            f"{old}'s default Local feed {report.demoted_default} is now an ordinary "
            f"Local feed of {new}"
        )
    for key in report.conflicts:
        click.echo(f"{key} kept: {new} has its own")
    if report.keys_kept:
        click.echo(
            f"{report.keys_kept} API keys left with {old} (dead while {old} is not "
            "configured; --move-keys moves them)"
        )
    click.echo(
        f"moved from {old} to {new}: {len(report.feeds)} Feeds, {report.keys} API keys, "
        f"{report.jobs} jobs, {len(report.files)} files; {len(report.conflicts)} conflicts"
    )


if __name__ == "__main__":
    main()
