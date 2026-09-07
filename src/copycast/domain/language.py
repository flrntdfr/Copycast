"""BCP 47 language tags as Copycast stores them (``fr``, ``pt-BR``)."""

from __future__ import annotations

import re
from typing import Final

LANGUAGE_RE: Final = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def normalize_language(value: str | None) -> str | None:
    """``fr_FR`` -> ``fr-FR``, blank -> ``None``; raises ``ValueError`` on nonsense."""
    if value is None:
        return None
    text = value.strip().replace("_", "-")
    if not text:
        return None
    parts = text.split("-")
    parts[0] = parts[0].lower()
    parts[1:] = [part.upper() if len(part) == 2 else part for part in parts[1:]]
    text = "-".join(parts)
    if not LANGUAGE_RE.match(text):
        raise ValueError(f"language must be a BCP 47 tag such as fr or pt-BR, got {value!r}")
    return text


__all__ = ["LANGUAGE_RE", "normalize_language"]
