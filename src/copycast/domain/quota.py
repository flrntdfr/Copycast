"""Storage quotas: sizes as the configuration writes them, and a User's quota state.

Units are decimal (1 GB = 1000**3 bytes), as disks and podcast apps count them. A
quota is a number of bytes or ``"unlimited"``; usage is the sum of the Owner's Feeds'
``storage_bytes``. ``near`` starts at 90 %, ``over`` at 100 % (so 0 of 0 is over).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

QuotaState = Literal["ok", "near", "over"]
QuotaLimit = int | Literal["unlimited"]

NEAR_RATIO: Final = 0.9
UNLIMITED: Final = "unlimited"

_UNITS: Final[dict[str, int]] = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
}
_SIZE_RE: Final = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s*$")
_SIZE_HELP: Final = 'a size is like "10 GB", "500 MB" or a number of bytes (decimal units)'


def parse_size(value: str | int) -> int:
    """Bytes from an int, or from ``"<number> <unit>"`` with B, kB, KB, MB, GB or TB.

    The space is optional and the number may be a decimal (``"1.5 TB"``). Binary units
    (GiB) and negative numbers are refused with ``ValueError``.
    """
    if isinstance(value, bool):
        raise ValueError(_SIZE_HELP)
    if isinstance(value, int):
        if value < 0:
            raise ValueError(_SIZE_HELP)
        return value
    text = value.strip()
    if text.isdigit():
        return int(text)
    match = _SIZE_RE.match(text)
    if match is None:
        raise ValueError(_SIZE_HELP)
    number, unit = match.groups()
    factor = _UNITS.get(unit.lower())
    if factor is None:
        raise ValueError(_SIZE_HELP)
    try:
        amount = Decimal(number) * factor
    except InvalidOperation as exc:  # pragma: no cover - the pattern admits only decimals
        raise ValueError(_SIZE_HELP) from exc
    return int(amount)


def parse_quota_limit(value: str | int) -> QuotaLimit:
    """``"unlimited"`` (any case) or :func:`parse_size`."""
    if isinstance(value, str) and value.strip().lower() == UNLIMITED:
        return UNLIMITED
    return parse_size(value)


def quota_bytes(limit: QuotaLimit) -> int | None:
    """The quota in bytes; None means unlimited."""
    return None if limit == UNLIMITED else int(limit)


def quota_state(used: int, quota: int | None) -> QuotaState:
    """``ok`` without a quota; ``over`` from 100 % (0 of 0 is over); ``near`` from 90 %."""
    if quota is None:
        return "ok"
    if used >= quota:
        return "over"
    if used >= NEAR_RATIO * quota:
        return "near"
    return "ok"


def remaining(used: int, quota: int | None) -> int | None:
    """Bytes left before the quota (never negative); None means unlimited."""
    if quota is None:
        return None
    return max(0, quota - used)


def format_size(n: int) -> str:
    """``"3.2 GB"``: decimal units, one decimal place above bytes."""
    if n < 1000:
        return f"{n} B"
    value = float(n)
    for unit in ("kB", "MB", "GB", "TB"):
        value /= 1000
        if round(value, 1) < 1000 or unit == "TB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = [
    "NEAR_RATIO",
    "UNLIMITED",
    "QuotaLimit",
    "QuotaState",
    "format_size",
    "parse_quota_limit",
    "parse_size",
    "quota_bytes",
    "quota_state",
    "remaining",
]
