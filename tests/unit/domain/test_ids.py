from __future__ import annotations

import hashlib
import re

import pytest

from copycast.domain.enums import AssetKind, AssetProvenance
from copycast.domain.ids import (
    FEED_ID_RE,
    INBOX_ALPHABET,
    asset_id,
    inbox_id,
    is_feed_id,
    item_id,
    mirror_id,
    slug,
)
from copycast.domain.urls import normalize_source_url

HEX16 = re.compile(r"^[0-9a-f]{16}$")


def test_mirror_id_is_deterministic_and_compact() -> None:
    a = mirror_id(normalize_source_url("https://example.com/feed.xml"))
    b = mirror_id(normalize_source_url("https://example.com/feed.xml"))
    assert a == b
    assert HEX16.match(a)
    assert a == hashlib.sha256(b"example.com/feed.xml").hexdigest()[:16]


def test_mirror_id_follows_the_dedup_key_not_the_raw_url() -> None:
    assert mirror_id(normalize_source_url("https://www.example.com/feed.xml/")) == mirror_id(
        normalize_source_url("http://EXAMPLE.com/feed.xml?utm_source=x")
    )
    assert mirror_id(normalize_source_url("https://example.com/a.xml")) != mirror_id(
        normalize_source_url("https://example.com/b.xml")
    )


def test_mirror_id_is_a_valid_feed_id() -> None:
    assert is_feed_id(mirror_id("example.com/feed.xml"))


def test_inbox_id_shape() -> None:
    ident = inbox_id("Copycast")
    stem, suffix = ident.rsplit("-", 1)
    assert stem == "copycast"
    assert len(suffix) == 6
    assert set(suffix) <= set(INBOX_ALPHABET)
    assert is_feed_id(ident)


def test_inbox_id_is_random_per_call() -> None:
    assert inbox_id("Later") != inbox_id("Later")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Copycast", "copycast"),
        ("  Read  Later!! ", "read-later"),
        ("Émissions à écouter", "missions-couter"),
        ("日本語", "inbox"),
        ("---", "inbox"),
        ("a" * 60, "a" * 40),
        ("word-" * 12, ("word-" * 12)[:40].rstrip("-")),
    ],
)
def test_slug(name: str, expected: str) -> None:
    assert slug(name) == expected
    assert len(slug(name)) <= 40


def test_inbox_id_never_exceeds_feed_id_length() -> None:
    ident = inbox_id("x" * 200)
    assert len(ident) <= 63
    assert FEED_ID_RE.match(ident)


def test_item_id_hashes_feed_and_trimmed_source_key() -> None:
    expected = hashlib.sha256(b"feed1\nurn:guid:1").hexdigest()[:16]
    assert item_id("feed1", "urn:guid:1") == expected
    assert item_id("feed1", "  urn:guid:1 \n") == expected
    assert item_id("feed2", "urn:guid:1") != expected
    assert item_id("feed1", "Youtube:abc") != item_id("feed1", "youtube:abc")


def test_asset_id_covers_the_uniqueness_tuple() -> None:
    feed_art = asset_id("feed1", None, AssetKind.artwork, None, None, AssetProvenance.mirrored)
    assert feed_art == hashlib.sha256(b"feed1\n\nartwork\n\n\nmirrored").hexdigest()[:16]
    item_art = asset_id("feed1", "item1", AssetKind.artwork, None, None, AssetProvenance.mirrored)
    assert item_art != feed_art
    en_vtt = asset_id("feed1", "item1", AssetKind.transcript, "en", "vtt", "mirrored")
    en_srt = asset_id("feed1", "item1", AssetKind.transcript, "en", "srt", "mirrored")
    en_vtt_gen = asset_id("feed1", "item1", AssetKind.transcript, "en", "vtt", "generated")
    assert len({en_vtt, en_srt, en_vtt_gen}) == 3
    assert HEX16.match(en_vtt)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("a", True),
        ("abc-123", True),
        ("0" * 63, True),
        ("0" * 64, False),
        ("-abc", False),
        ("ABC", False),
        ("a_b", False),
        ("", False),
        ("a.b", False),
    ],
)
def test_feed_id_regex(value: str, ok: bool) -> None:
    assert is_feed_id(value) is ok
