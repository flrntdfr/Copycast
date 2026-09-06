"""Process configuration.

Values come, in increasing precedence, from the built-in defaults, the TOML
file named by ``COPYCAST_CONFIG`` (missing file tolerated), environment
variables ``COPYCAST__<SECTION>__<KEY>`` and explicit constructor arguments.
A handful of process knobs (bind address, ports, log format, ...) are
environment-only and use the flat ``COPYCAST_<NAME>`` form.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from copycast.domain.engine_options import EngineOptionRejected, EngineOptions

DEFAULT_CONFIG_PATH = "./config/copycast.toml"
CONFIG_ENV = "COPYCAST_CONFIG"


class SettingsError(Exception):
    """Configuration is unusable; the message is one actionable line."""


class RefreshSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    interval_hours: int = Field(default=24, ge=1)
    fetch_cooldown_minutes: int = Field(default=15, ge=0)
    concurrency: int = Field(default=2, ge=1, le=32)


class EngineSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: Literal["nightly", "stable"] = "nightly"
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def _reject_owned(cls, value: dict[str, Any]) -> dict[str, Any]:
        # EngineOptionRejected is not a ValueError on purpose: pydantic would
        # otherwise fold it into a generic ValidationError and lose the keys.
        return EngineOptions.validate(value, scope="global")


AUTH_PASSWORD_MIN_LEN = 8
DEFAULT_AUTH_USERNAME = "copycast"


class AuthSettings(BaseModel):
    """The operator credential; setting ``password`` switches authentication on.

    With a password set, the web UI and the API require this pair (HTTP Basic),
    the published feeds accept it or the feed's own pair, and MCP requires an
    API key minted from the UI. Unset, Copycast is open (ADR 0004).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    username: str = DEFAULT_AUTH_USERNAME
    password: str | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        value = value.strip()
        if not value or ":" in value:
            raise ValueError("auth.username must not be blank or contain ':'")
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if len(value) < AUTH_PASSWORD_MIN_LEN:
            raise ValueError(f"auth.password must be at least {AUTH_PASSWORD_MIN_LEN} characters")
        return value

    @property
    def enabled(self) -> bool:
        return self.password is not None


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COPYCAST__",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
        validate_by_name=True,
        validate_by_alias=True,
    )

    base_url: str = "http://localhost:8080"
    data_dir: Path = Path("./data")
    database_url: str = "postgresql+psycopg://copycast:copycast@localhost:5432/copycast"
    refresh: RefreshSettings = Field(default_factory=RefreshSettings)
    engine: EngineSettings = Field(default_factory=EngineSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    # Environment-only knobs (flat COPYCAST_<NAME>, never read from the TOML file).
    bind: str = Field(default="0.0.0.0", validation_alias="COPYCAST_BIND")
    port: int = Field(default=8080, ge=1, le=65535, validation_alias="COPYCAST_PORT")
    worker_port: int = Field(default=8081, ge=1, le=65535, validation_alias="COPYCAST_WORKER_PORT")
    web_dir: Path | None = Field(default=None, validation_alias="COPYCAST_WEB_DIR")
    log_format: Literal["console", "json"] = Field(
        default="console", validation_alias="COPYCAST_LOG_FORMAT"
    )
    log_level: str = Field(default="INFO", validation_alias="COPYCAST_LOG_LEVEL")
    auto_migrate: bool = Field(default=True, validation_alias="COPYCAST_AUTO_MIGRATE")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_path()),
        )

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(
                "base_url must be an absolute http(s) URL such as http://localhost:8080, "
                f"got {value!r}"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def _rewrite_database_url(cls, value: str) -> str:
        value = value.strip()
        parts = urlsplit(value)
        scheme = parts.scheme.lower()
        if scheme in {"postgresql", "postgres"}:
            value = urlunsplit(("postgresql+psycopg", parts.netloc, parts.path, parts.query, ""))
        elif scheme != "postgresql+psycopg":
            raise ValueError(
                "database_url must use the postgresql:// or postgresql+psycopg:// scheme, "
                f"got {parts.scheme or value!r}"
            )
        return value

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                f"log_level must be DEBUG, INFO, WARNING, ERROR or CRITICAL, got {value!r}"
            )
        return level

    @model_validator(mode="after")
    def _check_ports(self) -> Settings:
        if self.port == self.worker_port:
            raise ValueError("COPYCAST_PORT and COPYCAST_WORKER_PORT must differ")
        return self

    def check_data_dir(self) -> Path:
        """Create the data directory when absent and prove it is writable.

        Raises SettingsError with a chown hint, the one actionable line the
        startup paths print before exiting 2.
        """
        path = self.data_dir
        # The api and the worker start together on a shared directory: a per-process
        # probe name keeps one process from unlinking the other's probe mid-check.
        probe = path / f".write-test.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(b"")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            uid = os.getuid() if hasattr(os, "getuid") else "copycast"
            raise SettingsError(
                f"data directory {path} is not writable ({exc.strerror or exc}); "
                f"run: chown -R {uid}:{uid} {path}"
            ) from exc
        return path

    def redacted(self) -> dict[str, Any]:
        """A JSON-safe dump with the database password masked."""
        data = self.model_dump(mode="json")
        data["database_url"] = redact_url(self.database_url)
        if self.auth.password is not None:
            data["auth"]["password"] = "***"
        return data


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.password:
        return url
    userinfo = parts.username or ""
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{userinfo}:***@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def get_settings(**overrides: Any) -> Settings:
    """Build Settings, turning any validation failure into a SettingsError."""
    try:
        return Settings(**overrides)
    except EngineOptionRejected as exc:
        raise SettingsError(str(exc)) from exc
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise SettingsError(_first_line(exc)) from exc


def _first_line(exc: ValueError) -> str:
    if isinstance(exc, ValidationError):
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            prefix = f"invalid configuration: {loc}" if loc else "invalid configuration"
            return f"{prefix}: {msg}"
    return f"invalid configuration: {exc}"
