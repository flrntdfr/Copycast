from __future__ import annotations

from pathlib import Path

import pytest

from copycast.settings import Settings, SettingsError, get_settings, redact_url


def test_defaults_match_the_plan(tmp_path: Path) -> None:
    settings = get_settings(storage={"scratch_dir": tmp_path})
    assert settings.base_url == "http://localhost:8080"
    assert settings.database_url == "postgresql+psycopg://copycast:copycast@localhost:5432/copycast"
    assert settings.refresh.interval_hours == 24
    assert settings.refresh.fetch_cooldown_minutes == 5
    assert settings.refresh.concurrency == 2
    assert settings.engine.channel == "nightly"
    assert settings.engine.options == {}
    assert (settings.bind, settings.port, settings.worker_port) == ("0.0.0.0", 8080, 8081)
    assert settings.auto_migrate is True
    assert settings.log_format == "console"
    assert settings.storage.url == "s3://copycast" and settings.storage.durable
    assert settings.storage.endpoint == "" and settings.storage.region == "us-east-1"
    assert settings.storage.scratch_dir == tmp_path.resolve()
    assert settings.cache.url is None


def test_toml_file_then_env_then_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "copycast.toml"
    config.write_text(
        'base_url = "http://toml.example"\n'
        'database_url = "postgresql://a:b@toml/db"\n'
        "[refresh]\ninterval_hours = 6\nconcurrency = 3\n"
        '[engine]\nchannel = "stable"\n[engine.options]\nratelimit = 5\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COPYCAST_CONFIG", str(config))
    monkeypatch.setenv("COPYCAST__REFRESH__CONCURRENCY", "4")
    monkeypatch.setenv("COPYCAST__BASE_URL", "http://env.example/")
    monkeypatch.setenv("COPYCAST_PORT", "9090")
    settings = get_settings(storage={"scratch_dir": tmp_path}, refresh={"interval_hours": 1})
    assert settings.base_url == "http://env.example"
    assert settings.database_url == "postgresql+psycopg://a:b@toml/db"
    assert settings.refresh.concurrency == 4
    assert settings.refresh.interval_hours == 1
    assert settings.refresh.fetch_cooldown_minutes == 5
    assert settings.engine.channel == "stable"
    assert settings.engine.options == {"ratelimit": 5}
    assert settings.port == 9090


def test_missing_config_file_is_tolerated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST_CONFIG", str(tmp_path / "absent.toml"))
    assert get_settings(storage={"scratch_dir": tmp_path}).base_url == "http://localhost:8080"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
        ("postgres://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_database_url_is_rewritten_for_psycopg(raw: str, expected: str, tmp_path: Path) -> None:
    assert (
        get_settings(storage={"scratch_dir": tmp_path}, database_url=raw).database_url == expected
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_url": "mysql://u:p@h/db"},
        {"database_url": "postgresql+asyncpg://u:p@h/db"},
        {"base_url": "localhost:8080"},
        {"base_url": "ftp://x"},
        {"refresh": {"concurrency": 0}},
        {"refresh": {"interval_hours": 0}},
        {"engine": {"channel": "beta"}},
        {"engine": {"options": {"outtmpl": "x"}}},
        {"port": 8081},
        {"log_level": "LOUD"},
    ],
)
def test_invalid_configuration_is_one_settings_error(
    overrides: dict[str, object], tmp_path: Path
) -> None:
    with pytest.raises(SettingsError) as excinfo:
        get_settings(storage={"scratch_dir": tmp_path}, **overrides)
    message = str(excinfo.value)
    assert "\n" not in message
    assert message


def test_rejected_engine_option_names_the_key(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"\[engine\.options\].*outtmpl"):
        get_settings(
            storage={"scratch_dir": tmp_path}, engine={"options": {"outtmpl": "x", "ratelimit": 1}}
        )


def test_base_url_trailing_slash_is_dropped(tmp_path: Path) -> None:
    assert get_settings(
        storage={"scratch_dir": tmp_path}, base_url="https://x.example/"
    ).base_url == ("https://x.example")


def test_redacted_hides_the_password(tmp_path: Path) -> None:
    settings = get_settings(
        storage={"scratch_dir": tmp_path}, database_url="postgresql://user:s3cret@db:5432/x"
    )
    dump = settings.redacted()
    assert dump["database_url"] == "postgresql+psycopg://user:***@db:5432/x"
    assert "s3cret" not in str(dump)
    assert redact_url("postgresql://db/x") == "postgresql://db/x"
    assert redact_url("postgresql://u:p@[::1]:5432/x") == "postgresql://u:***@[::1]:5432/x"


def test_check_scratch_dir_creates_and_reports_unwritable(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "scratch"
    settings = get_settings(storage={"scratch_dir": target})
    assert settings.check_scratch_dir() == target.resolve()
    assert target.is_dir()

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        unwritable = Settings(storage={"scratch_dir": blocked}, base_url="http://x")
        with pytest.raises(SettingsError, match="chown"):
            unwritable.check_scratch_dir()
    finally:
        blocked.chmod(0o700)


def test_storage_and_cache_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST__STORAGE__URL", "s3://archive/copycast/")
    monkeypatch.setenv("COPYCAST__STORAGE__ENDPOINT", "http://seaweedfs:8333/")
    monkeypatch.setenv("COPYCAST__STORAGE__SECRET_ACCESS_KEY", "sup3rsecret")
    monkeypatch.setenv("COPYCAST__CACHE__URL", "valkey://:cachepass@valkey:6379/0")
    settings = get_settings(storage={"scratch_dir": tmp_path})
    assert settings.storage.url == "s3://archive/copycast"
    assert settings.storage.endpoint == "http://seaweedfs:8333"
    assert settings.cache.url == "valkey://:cachepass@valkey:6379/0"
    dump = settings.redacted()
    assert dump["storage"]["secret_access_key"] == "***"
    assert "sup3rsecret" not in str(dump) and "cachepass" not in str(dump)
    assert get_settings(storage={"url": "memory://x"}).storage.durable is False
    assert get_settings(cache={"url": "  "}).cache.url is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage": {"url": "/data"}},
        {"storage": {"url": "gs://bucket"}},
        {"storage": {"url": "s3://"}},
        {"storage": {"url": "s3://b", "endpoint": "seaweedfs:8333"}},
        {"cache": {"url": "memcached://cache:11211"}},
    ],
)
def test_bad_storage_or_cache_settings_are_one_line(overrides: dict[str, object]) -> None:
    with pytest.raises(SettingsError, match=r"invalid configuration: (storage|cache)"):
        get_settings(**overrides)


def test_auth_is_off_by_default_and_on_with_a_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = get_settings(storage={"scratch_dir": tmp_path})
    assert off.auth.enabled is False and off.auth.username == "copycast"
    assert off.redacted()["auth"] == {"username": "copycast", "password": None}

    monkeypatch.setenv("COPYCAST__AUTH__PASSWORD", "correct-horse")
    monkeypatch.setenv("COPYCAST__AUTH__USERNAME", " florent ")
    on = get_settings(storage={"scratch_dir": tmp_path})
    assert on.auth.enabled is True
    assert (on.auth.username, on.auth.password) == ("florent", "correct-horse")
    assert on.redacted()["auth"] == {"username": "florent", "password": "***"}

    # An empty or blank password is "unset", not a weak password.
    assert (
        get_settings(storage={"scratch_dir": tmp_path}, auth={"password": ""}).auth.enabled is False
    )
    assert (
        get_settings(storage={"scratch_dir": tmp_path}, auth={"password": "        "}).auth.enabled
        is False
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth": {"password": "short"}}, "at least 8 characters"),
        ({"auth": {"username": "a:b", "password": "correct-horse"}}, "contain ':'"),
        ({"auth": {"username": "  ", "password": "correct-horse"}}, "blank"),
    ],
)
def test_auth_settings_are_validated(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        get_settings(storage={"scratch_dir": tmp_path}, **overrides)
