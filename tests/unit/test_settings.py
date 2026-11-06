from __future__ import annotations

import json
from pathlib import Path

import pytest

from copycast.domain.enums import AuthMode
from copycast.settings import Settings, SettingsError, get_settings, redact_url
from tests.support.users import TEST_HASHER


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
    assert off.auth.mode is AuthMode.open
    assert off.redacted()["auth"] == {
        "username": "copycast",
        "password": None,
        "users": [],
        "default_quota": "unlimited",
        "session_days": 30,
        "owner_of_existing": None,
    }

    monkeypatch.setenv("COPYCAST__AUTH__PASSWORD", "correct-horse")
    monkeypatch.setenv("COPYCAST__AUTH__USERNAME", " florent ")
    on = get_settings(storage={"scratch_dir": tmp_path})
    assert on.auth.enabled is True
    assert (on.auth.username, on.auth.password) == ("florent", "correct-horse")
    assert on.auth.mode is AuthMode.legacy
    assert on.redacted()["auth"]["password"] == "***"

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


def test_security_exempts_nothing_by_default(tmp_path: Path) -> None:
    settings = get_settings(storage={"scratch_dir": tmp_path})
    assert settings.security.allow_private_hosts == []
    policy = settings.security.destination_policy
    assert policy.allowed_hosts == frozenset() and policy.allowed_networks == ()


def test_security_allow_private_hosts_from_toml_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "copycast.toml"
    config.write_text(
        '[security]\nallow_private_hosts = ["nas.lan", "192.168.1.0/24"]\n', encoding="utf-8"
    )
    monkeypatch.setenv("COPYCAST_CONFIG", str(config))
    from_file = get_settings(storage={"scratch_dir": tmp_path})
    assert from_file.security.allow_private_hosts == ["nas.lan", "192.168.1.0/24"]
    monkeypatch.setenv(
        "COPYCAST__SECURITY__ALLOW_PRIVATE_HOSTS", '["fixtures", " 10.0.0.0/8 ", ""]'
    )
    from_env = get_settings(storage={"scratch_dir": tmp_path})
    assert from_env.security.allow_private_hosts == ["fixtures", "10.0.0.0/8"]
    policy = from_env.security.destination_policy
    assert policy.allows_host("fixtures")
    assert [str(n) for n in policy.allowed_networks] == ["10.0.0.0/8"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"security": {"allow_private_hosts": ["http://nas.lan"]}},
        {"security": {"allow_private_hosts": ["not a host"]}},
        {"security": {"allow_private_hosts": "nas.lan"}},
        {"security": {"allow_everything": True}},
    ],
)
def test_bad_security_settings_are_one_line(overrides: dict[str, object]) -> None:
    with pytest.raises(SettingsError, match=r"^invalid configuration: security"):
        get_settings(**overrides)


def test_forwarded_allow_ips_is_unset_by_default(tmp_path: Path) -> None:
    assert get_settings(storage={"scratch_dir": tmp_path}).forwarded_allow_ips is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.0.0.5", "10.0.0.5"),
        (" 172.16.0.0/12 , 10.0.0.0/8,::1 ", "172.16.0.0/12,10.0.0.0/8,::1"),
        ("fd00::/8", "fd00::/8"),
        ("*", "*"),
        ("  ", None),
    ],
)
def test_forwarded_allow_ips_from_the_environment(
    raw: str, expected: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPYCAST_FORWARDED_ALLOW_IPS", raw)
    assert get_settings(storage={"scratch_dir": tmp_path}).forwarded_allow_ips == expected


@pytest.mark.parametrize("raw", ["10.0.0.0/33", "traefik", "10.0.0.1,proxy.local", "1.2.3"])
def test_a_bad_forwarded_allow_ips_is_one_settings_error(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPYCAST_FORWARDED_ALLOW_IPS", raw)
    with pytest.raises(SettingsError, match="COPYCAST_FORWARDED_ALLOW_IPS"):
        get_settings(storage={"scratch_dir": tmp_path})


def test_forwarded_allow_ips_is_never_read_from_the_toml_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "copycast.toml"
    config.write_text(
        'forwarded_allow_ips = "*"\nCOPYCAST_FORWARDED_ALLOW_IPS = "*"\n', encoding="utf-8"
    )
    monkeypatch.setenv("COPYCAST_CONFIG", str(config))
    assert get_settings(storage={"scratch_dir": tmp_path}).forwarded_allow_ips is None
    monkeypatch.setenv("COPYCAST_FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    assert get_settings(storage={"scratch_dir": tmp_path}).forwarded_allow_ips == "10.0.0.0/8"


def test_allowed_hosts_is_unset_by_default(tmp_path: Path) -> None:
    settings = get_settings(storage={"scratch_dir": tmp_path})
    assert settings.allowed_hosts is None
    assert settings.allowed_host_names == frozenset()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("copycast", {"copycast"}),
        (
            " Copycast.Local. , nas_box,copycast.tail1234.ts.net ",
            {"copycast.local", "nas_box", "copycast.tail1234.ts.net"},
        ),
        ("::1,10.0.0.5", {"::1", "10.0.0.5"}),
        ("*", {"*"}),
        ("  ", set()),
    ],
)
def test_allowed_hosts_from_the_environment(
    raw: str, expected: set[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPYCAST_ALLOWED_HOSTS", raw)
    assert get_settings(storage={"scratch_dir": tmp_path}).allowed_host_names == expected


@pytest.mark.parametrize(
    "raw", ["https://copycast.example", "copycast:8080", "copycast/x", "-bad.example", "a..b"]
)
def test_a_bad_allowed_hosts_is_one_settings_error(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPYCAST_ALLOWED_HOSTS", raw)
    with pytest.raises(SettingsError, match="COPYCAST_ALLOWED_HOSTS"):
        get_settings(storage={"scratch_dir": tmp_path})


# --------------------------------------------------------------------------- users mode

HASH = TEST_HASHER.hash("alice-password")
OTHER_HASH = TEST_HASHER.hash("bob-password")


def _user(username: str, **extra: object) -> dict[str, object]:
    return {"username": username, "password_hash": HASH, **extra}


def test_users_from_the_toml_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "copycast.toml"
    config.write_text(
        "[auth]\n"
        'default_quota = "10 GB"\n'
        "session_days = 7\n"
        'owner_of_existing = "bob"\n'
        "[[auth.users]]\n"
        'username = "alice"\n'
        f"password_hash = '{HASH}'\n"
        'totp_secret = "jbsw y3dp ehpk 3pxp"\n'
        "admin = true\n"
        'quota = "unlimited"\n'
        "[[auth.users]]\n"
        'username = "bob"\n'
        f"password_hash = '{OTHER_HASH}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COPYCAST_CONFIG", str(config))
    settings = get_settings(storage={"scratch_dir": tmp_path})
    auth = settings.auth
    assert auth.mode is AuthMode.users and auth.enabled
    assert [u.username for u in auth.users] == ["alice", "bob"]
    assert auth.users[0].totp_secret == "JBSWY3DPEHPK3PXP"  # spaces dropped, upper-cased
    assert auth.default_quota == 10_000_000_000 and auth.session_days == 7
    assert auth.quota_for("alice") == "unlimited"
    assert auth.quota_for("bob") == 10_000_000_000
    assert auth.quota_for("removed") == 10_000_000_000
    assert auth.effective_owner_of_existing == "bob"
    redacted = settings.redacted()["auth"]
    assert [u["password_hash"] for u in redacted["users"]] == ["***", "***"]
    assert [u["totp_secret"] for u in redacted["users"]] == ["***", None]
    assert HASH not in str(settings.redacted())


def test_an_env_list_replaces_the_toml_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "copycast.toml"
    config.write_text(
        "[auth]\nsession_days = 9\n[[auth.users]]\n"
        f"username = 'toml'\npassword_hash = '{HASH}'\nadmin = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COPYCAST_CONFIG", str(config))
    entries = [_user("env", admin=True), {"username": "env2", "password_hash": OTHER_HASH}]
    monkeypatch.setenv("COPYCAST__AUTH__USERS", json.dumps(entries))
    auth = get_settings(storage={"scratch_dir": tmp_path}).auth
    assert [u.username for u in auth.users] == ["env", "env2"]
    assert auth.session_days == 9  # the rest of [auth] still comes from the file
    assert auth.effective_owner_of_existing == "env"  # the first Admin in file order


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        ({"users": [_user("Alice", admin=True)]}, r"auth\.users\[0\]\.username: must be 1-32"),
        ({"users": [_user("-x", admin=True)]}, "starting with a letter or digit"),
        ({"users": [_user("a" * 33, admin=True)]}, "must be 1-32"),
        (
            {"users": [{"username": "a", "password_hash": "hunter2hunter2", "admin": True}]},
            r"password_hash: must be an argon2id PHC string \(run: copycast users hash-password\)",
        ),
        (
            {"users": [{"username": "a", "password": "hunter2hunter2", "admin": True}]},
            "a plaintext password is refused",
        ),
        (
            {"users": [_user("a", admin=True, password_hash=HASH.replace("argon2id", "argon2i"))]},
            "argon2id PHC",
        ),
        ({"users": [_user("a", admin=True, totp_secret="not base32!")]}, "totp_secret: must be"),
        ({"users": [_user("a", admin=True, totp_secret="JBSWY3DP")]}, "at least 16"),
        ({"users": [_user("a", admin=True, quota="10 GiB")]}, 'quota: must be like "10 GB"'),
        ({"default_quota": "lots"}, "default_quota: must be like"),
        ({"users": [_user("a", admin=True), _user("a")]}, 'duplicate username "a"'),
        ({"users": [_user("a"), _user("b")]}, "at least one user needs admin = true"),
        (
            {"users": [_user("a", admin=True)], "password": "correct-horse"},
            "auth.password and auth.users are exclusive",
        ),
        (
            {"users": [_user("a", admin=True)], "owner_of_existing": "zed"},
            'owner_of_existing names no configured user: "zed"',
        ),
        ({"owner_of_existing": "zed"}, "must be auth.username without auth.users"),
        ({"session_days": 0}, "session_days"),
        ({"users": [_user("a", admin=True, role="admin")]}, "role"),
    ],
)
def test_users_are_validated(tmp_path: Path, auth: dict[str, object], message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        get_settings(storage={"scratch_dir": tmp_path}, auth=auth)


def test_owner_of_existing_in_open_and_legacy_mode(tmp_path: Path) -> None:
    open_mode = get_settings(storage={"scratch_dir": tmp_path}, auth={"username": "Me@Example.org"})
    assert open_mode.auth.legacy_owner == "me-example.org"
    assert open_mode.auth.effective_owner_of_existing == "me-example.org"
    legacy = get_settings(
        storage={"scratch_dir": tmp_path},
        auth={"username": "Florent", "password": "correct-horse", "owner_of_existing": "florent"},
    )
    assert legacy.auth.mode is AuthMode.legacy
    assert legacy.auth.effective_owner_of_existing == "florent"
    assert legacy.auth.quota_for("florent") == "unlimited"


def test_a_global_cookiefile_is_refused_with_users(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"engine\.options\.cookiefile cannot be set"):
        get_settings(
            storage={"scratch_dir": tmp_path},
            auth={"users": [_user("a", admin=True)]},
            engine={"options": {"cookiefile": "/cookies.txt"}},
        )
    # Outside users mode it stays possible.
    settings = get_settings(
        storage={"scratch_dir": tmp_path}, engine={"options": {"cookiefile": "/cookies.txt"}}
    )
    assert settings.engine.options["cookiefile"] == "/cookies.txt"
