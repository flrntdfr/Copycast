"""Natural file-name order: "Chapter 2" before "Chapter 10", case ignored.

Uploads added to a Bucket arrive in this order (ADR 0018); the web UI sorts
the files it sends the same way.
"""

from __future__ import annotations

import re
from typing import Final

_DIGITS: Final = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple[list[str | int], str]:
    """A sort key: text runs case-folded, digit runs as numbers, the name itself last."""
    parts = _DIGITS.split(name)
    # split() alternates text and digits, text first, so equal positions compare like types.
    key: list[str | int] = [int(p) if i % 2 else p.casefold() for i, p in enumerate(parts)]
    return key, name


__all__ = ["natural_key"]
