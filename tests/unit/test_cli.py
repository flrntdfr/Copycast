from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from copycast.application.passwords import PasswordHasher
from copycast.cli import main
from copycast.domain.totp import decode_secret
from copycast.settings import get_settings
from copycast.version import APP_VERSION


def test_version_prints_app_and_engine() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == f"copycast {APP_VERSION}"
    assert lines[1].startswith("engine ")
    assert APP_VERSION == "2.3.0"


def test_config_prints_redacted_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST__DATABASE_URL", "postgresql://copycast:hunter2@db/copycast")
    monkeypatch.setenv("COPYCAST__STORAGE__SCRATCH_DIR", str(tmp_path))
    monkeypatch.setenv("COPYCAST__STORAGE__SECRET_ACCESS_KEY", "s3kr3t-key")
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["database_url"] == "postgresql+psycopg://copycast:***@db/copycast"
    assert "hunter2" not in result.output
    assert data["refresh"]["interval_hours"] == 24
    assert data["storage"]["scratch_dir"] == str(tmp_path.resolve())
    assert data["storage"]["secret_access_key"] == "***"
    assert "s3kr3t-key" not in result.output


def test_invalid_config_exits_2_with_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPYCAST__BASE_URL", "not-a-url")
    result = CliRunner().invoke(main, ["config"])
    assert result.exit_code == 2
    assert result.output.count("\n") == 1
    assert result.output.startswith("copycast: invalid configuration")


def test_every_plan_command_is_registered() -> None:
    assert set(main.commands) == {
        "api",
        "worker",
        "migrate",
        "rebuild",
        "storage",
        "config",
        "openapi",
        "users",
    }
    result = CliRunner().invoke(main, ["rebuild", "--help"])
    assert result.exit_code == 0 and "--yes" in result.output


@pytest.mark.parametrize(
    ("env", "expected"), [(None, None), ("172.18.0.0/16,10.0.0.2", "172.18.0.0/16,10.0.0.2")]
)
def test_api_trusts_forwarded_headers_only_from_the_configured_proxies(
    env: str | None, expected: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    import copycast.cli as cli

    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("COPYCAST__STORAGE__SCRATCH_DIR", str(tmp_path))
    if env is None:
        monkeypatch.delenv("COPYCAST_FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("COPYCAST_FORWARDED_ALLOW_IPS", env)
    monkeypatch.setattr(cli, "configure_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_api_preflight", lambda settings: None)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(main, ["api"])
    assert result.exit_code == 0, result.output
    (kwargs,) = calls
    assert kwargs["proxy_headers"] is True
    assert kwargs["forwarded_allow_ips"] == expected


def test_users_hash_password_from_stdin_verifies() -> None:
    result = CliRunner().invoke(main, ["users", "hash-password", "--stdin"], input="s3cret pass\n")
    assert result.exit_code == 0, result.output
    phc = result.output.strip()
    assert phc.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert PasswordHasher().verify(phc, "s3cret pass")
    assert not PasswordHasher().verify(phc, "s3cret pass\n")
    # The hash is what the settings accept for password_hash.
    settings = get_settings(
        auth={"users": [{"username": "a", "password_hash": phc, "admin": True}]}
    )
    assert settings.auth.users[0].password_hash == phc


def test_users_hash_password_prompts_twice_and_refuses_short_ones() -> None:
    prompted = CliRunner().invoke(
        main, ["users", "hash-password"], input="long enough\nlong enough\n"
    )
    assert prompted.exit_code == 0, prompted.output
    assert PasswordHasher().verify(prompted.output.strip().splitlines()[-1], "long enough")
    short = CliRunner().invoke(main, ["users", "hash-password", "--stdin"], input="short\n")
    assert short.exit_code == 2 and "at least 8 characters" in short.output


def test_users_totp_prints_the_secret_the_uri_and_a_qr_code() -> None:
    result = CliRunner().invoke(main, ["users", "totp", "florent"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    secret = lines[0].removeprefix("secret: ")
    assert len(secret) == 32 and decode_secret(secret)
    assert lines[1] == (
        f"uri:    otpauth://totp/Copycast:florent?secret={secret}"
        "&issuer=Copycast&digits=6&period=30"
    )
    assert any("\u2588" in line for line in lines[2:])  # the QR code's blocks
    assert lines[-1].endswith(f'totp_secret = "{secret}"')

    given = CliRunner().invoke(main, ["users", "totp", "alice", "--secret", "jbsw y3dp ehpk 3pxp"])
    assert given.exit_code == 0 and given.output.startswith("secret: JBSWY3DPEHPK3PXP\n")
    for args in (["Alice"], ["alice", "--secret", "short"], ["alice", "--secret", "not base32!!"]):
        refused = CliRunner().invoke(main, ["users", "totp", *args])
        assert refused.exit_code == 2, args
