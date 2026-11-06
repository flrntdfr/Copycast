"""TOTP: RFC 4226 and RFC 6238 (SHA-1) vectors, the window, replay refusal, secrets and URIs."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlsplit

import pytest

from copycast.domain import totp

RFC_KEY = b"12345678901234567890"


@pytest.mark.parametrize(
    ("counter", "code"),
    list(
        enumerate(
            [
                "755224",
                "287082",
                "359152",
                "969429",
                "338314",
                "254676",
                "287922",
                "162583",
                "399871",
                "520489",
            ]
        )
    ),
)
def test_hotp_matches_rfc_4226_appendix_d(counter: int, code: str) -> None:
    assert totp.hotp(RFC_KEY, counter) == code


@pytest.mark.parametrize(
    ("now", "code"),
    [
        # RFC 6238 Appendix B, SHA-1 column, last six of the eight digits.
        (59, "287082"),
        (1111111109, "081804"),
        (1111111111, "050471"),
        (1234567890, "005924"),
        (2000000000, "279037"),
        (20000000000, "353130"),
    ],
)
def test_totp_matches_rfc_6238_appendix_b(now: int, code: str) -> None:
    assert totp.hotp(RFC_KEY, totp.step_at(now)) == code
    assert totp.match_step(RFC_KEY, code, now=now, last_step=None) == totp.step_at(now)


def test_step_at_counts_thirty_second_steps() -> None:
    assert totp.step_at(0) == 0 and totp.step_at(29.9) == 0 and totp.step_at(30) == 1


def test_match_step_accepts_one_step_either_side_only() -> None:
    now = 1_700_000_000.0
    step = totp.step_at(now)
    for offset in (-1, 0, 1):
        code = totp.hotp(RFC_KEY, step + offset)
        assert totp.match_step(RFC_KEY, code, now=now, last_step=None) == step + offset
    for offset in (-2, 2):
        code = totp.hotp(RFC_KEY, step + offset)
        assert totp.match_step(RFC_KEY, code, now=now, last_step=None) is None


def test_a_replayed_or_older_code_is_refused() -> None:
    now = 1_700_000_000.0
    step = totp.step_at(now)
    code = totp.hotp(RFC_KEY, step)
    assert totp.match_step(RFC_KEY, code, now=now, last_step=step - 1) == step
    assert totp.match_step(RFC_KEY, code, now=now, last_step=step) is None
    older = totp.hotp(RFC_KEY, step - 1)
    assert totp.match_step(RFC_KEY, older, now=now, last_step=step) is None


def test_match_step_ignores_spaces_and_refuses_malformed_codes() -> None:
    now = 59.0
    assert totp.match_step(RFC_KEY, "287 082", now=now, last_step=None) == 1
    for bad in ("", "28708", "2870822", "abcdef", "28708x"):
        assert totp.match_step(RFC_KEY, bad, now=now, last_step=None) is None
    assert totp.match_step(RFC_KEY, "000000", now=now, last_step=None) is None


def test_match_step_never_checks_negative_steps() -> None:
    # At t=0 the window would include step -1; it is skipped instead of packed.
    assert totp.match_step(RFC_KEY, totp.hotp(RFC_KEY, 0), now=0, last_step=None) == 0


def test_generate_secret_is_unpadded_base32_of_twenty_bytes() -> None:
    secret = totp.generate_secret()
    assert len(secret) == 32 and "=" not in secret
    assert len(totp.decode_secret(secret)) == totp.SECRET_BYTES
    assert totp.generate_secret() != secret


def test_decode_secret_tolerates_spaces_case_and_padding() -> None:
    secret = base64.b32encode(RFC_KEY).decode("ascii")
    assert totp.decode_secret(secret) == RFC_KEY
    spaced = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4)).lower()
    assert totp.decode_secret(spaced) == RFC_KEY
    assert totp.decode_secret("JBSWY3DPEHPK3PXP") == b"Hello!\xde\xad\xbe\xef"


@pytest.mark.parametrize("bad", ["", "   ", "====", "not base32!", "1111"])
def test_decode_secret_refuses_garbage(bad: str) -> None:
    with pytest.raises(ValueError, match="TOTP secret"):
        totp.decode_secret(bad)


def test_provisioning_uri_shape() -> None:
    uri = totp.provisioning_uri("jbsw y3dp ehpk 3pxp", "florent")
    parts = urlsplit(uri)
    assert parts.scheme == "otpauth" and parts.netloc == "totp"
    assert parts.path == "/Copycast:florent"
    assert parse_qs(parts.query) == {
        "secret": ["JBSWY3DPEHPK3PXP"],
        "issuer": ["Copycast"],
        "digits": ["6"],
        "period": ["30"],
    }
    assert uri.startswith("otpauth://totp/Copycast:florent?secret=JBSWY3DPEHPK3PXP&issuer=")


@pytest.mark.parametrize("digits", ["١٢٣٤٥٦", "１２３４５６", "۱۲۳۴۵۶", "12345٦"])
def test_match_step_refuses_non_ascii_digits_without_raising(digits: str) -> None:
    # str.isdigit() accepts these, and hmac.compare_digest raises TypeError on them.
    assert digits.isdigit()
    assert totp.match_step(RFC_KEY, digits, now=59, last_step=None) is None
