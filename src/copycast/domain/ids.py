"""Deterministic identifiers.

Mirror, item and asset ids are hashes of their natural keys so that
``copycast rebuild`` reproduces them from disk; Inbox ids carry a random
suffix because two Inboxes may share a name.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Final

FEED_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
INBOX_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"
INBOX_SUFFIX_LEN: Final = 6
SLUG_MAX_LEN: Final = 40
HASH_ID_LEN: Final = 16

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_ID_LEN]


def slug(name: str, max_len: int = SLUG_MAX_LEN) -> str:
    """Lowercase ASCII slug: runs of non-alphanumerics become one dash, edges trimmed."""
    text = name.lower().encode("ascii", "ignore").decode("ascii")
    text = _NON_SLUG.sub("-", text).strip("-")
    text = text[:max_len].rstrip("-")
    return text or "inbox"


def mirror_id(dedup_key: str) -> str:
    """``sha256(dedup_key)[:16]`` of the normalized Source URL."""
    return _short_hash(dedup_key)


def inbox_id(name: str) -> str:
    """``slug(name)[:40] + '-' + 6 chars`` from the unambiguous alphabet."""
    suffix = "".join(secrets.choice(INBOX_ALPHABET) for _ in range(INBOX_SUFFIX_LEN))
    return f"{slug(name)}-{suffix}"


def item_id(feed_id: str, source_key: str) -> str:
    """``sha256(feed_id + '\\n' + source_key.strip())[:16]``; also the on-disk stem."""
    return _short_hash(f"{feed_id}\n{source_key.strip()}")


def asset_id(
    feed_id: str,
    item_id: str | None,
    kind: str,
    language: str | None,
    format: str | None,
    provenance: str,
) -> str:
    """Hash of the asset's uniqueness tuple; ``item_id`` None means feed Artwork."""
    parts = [feed_id, item_id or "", str(kind), language or "", format or "", str(provenance)]
    return _short_hash("\n".join(parts))


def is_feed_id(value: str) -> bool:
    return FEED_ID_RE.match(value) is not None
