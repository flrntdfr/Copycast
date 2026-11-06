"""The 3.0 problem types: slugs, statuses and extension members."""

from __future__ import annotations

from copycast.domain.exceptions import (
    LOGIN_FAILED_DETAIL,
    DomainError,
    Forbidden,
    HttpsRequired,
    LoginBusy,
    LoginFailed,
    LoginLocked,
    OverQuota,
    Unauthorized,
)


def test_forbidden() -> None:
    error = Forbidden("admins only")
    assert (error.slug, error.status, str(error)) == ("forbidden", 403, "admins only")


def test_over_quota_carries_usage() -> None:
    error = OverQuota(used_bytes=11, quota_bytes=10)
    assert (error.slug, error.status) == ("over-quota", 507)
    assert error.extras == {"used_bytes": 11, "quota_bytes": 10}
    assert "over quota" in str(error)
    assert str(OverQuota(1, 0, "upload too large")) == "upload too large"


def test_login_failed_is_one_generic_401() -> None:
    error = LoginFailed()
    assert isinstance(error, Unauthorized)
    assert (error.slug, error.status, str(error)) == ("login-failed", 401, LOGIN_FAILED_DETAIL)
    assert str(error) == "wrong username, password or code"


def test_login_locked_and_busy_carry_retry_after() -> None:
    locked = LoginLocked(retry_after=60)
    assert (locked.slug, locked.status, locked.retry_after) == ("login-locked", 429, 60)
    assert locked.extras == {"retry_after": 60}
    busy = LoginBusy()
    assert (busy.slug, busy.status, busy.retry_after) == ("login-busy", 503, 5)
    assert busy.extras == {"retry_after": 5}


def test_https_required_names_the_base_url() -> None:
    error = HttpsRequired("https://copycast.example")
    assert isinstance(error, DomainError)
    assert (error.slug, error.status) == ("https-required", 400)
    assert str(error) == "sign in over https: https://copycast.example"
