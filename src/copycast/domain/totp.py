"""Time-based one-time passwords (RFC 6238 over RFC 4226 HOTP), standard library only.

Six digits, 30-second steps, HMAC-SHA1, one step of tolerance either side. A code is
accepted once: :func:`match_step` returns the step that matched, which the caller
stores, and it refuses any step not later than the stored one, so a replayed code fails.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Final
from urllib.parse import quote, urlencode

STEP_SECONDS: Final = 30
DIGITS: Final = 6
WINDOW: Final = 1
SECRET_BYTES: Final = 20
ISSUER: Final = "Copycast"


def generate_secret() -> str:
    """20 random bytes as unpadded base32 (32 characters)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def decode_secret(secret: str) -> bytes:
    """The key of a base32 secret; spaces, case and missing padding are tolerated."""
    text = "".join(secret.split()).upper().rstrip("=")
    if not text:
        raise ValueError("a TOTP secret is base32 and cannot be empty")
    padded = text + "=" * (-len(text) % 8)
    try:
        return base64.b32decode(padded, casefold=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("a TOTP secret must be base32") from exc


def hotp(key: bytes, counter: int) -> str:
    """RFC 4226: HMAC-SHA1 of the 8-byte counter, dynamically truncated to six digits."""
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**DIGITS).zfill(DIGITS)


def step_at(now: float) -> int:
    """The 30-second step a Unix time falls in."""
    return int(now // STEP_SECONDS)


def match_step(key: bytes, code: str, *, now: float, last_step: int | None) -> int | None:
    """The step in ``now ± 1`` whose code is ``code``, if it is later than ``last_step``.

    Spaces in ``code`` are ignored. Every candidate step is compared in constant time;
    a code from a step already used (or older) returns None, as does a wrong code.
    """
    typed = "".join(code.split())
    if len(typed) != DIGITS or not typed.isdigit():
        return None
    current = step_at(now)
    matched: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if step < 0:
            continue
        if hmac.compare_digest(hotp(key, step), typed) and (last_step is None or step > last_step):
            matched = step if matched is None else max(matched, step)
    return matched


def provisioning_uri(secret: str, username: str, issuer: str = ISSUER) -> str:
    """``otpauth://totp/Copycast:{username}?secret=…&issuer=Copycast&digits=6&period=30``."""
    label = quote(f"{issuer}:{username}", safe=":@")
    query = urlencode(
        {
            "secret": "".join(secret.split()).upper().rstrip("="),
            "issuer": issuer,
            "digits": DIGITS,
            "period": STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"


__all__ = [
    "DIGITS",
    "ISSUER",
    "SECRET_BYTES",
    "STEP_SECONDS",
    "WINDOW",
    "decode_secret",
    "generate_secret",
    "hotp",
    "match_step",
    "provisioning_uri",
    "step_at",
]
