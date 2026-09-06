"""Flat extraction of a pinned public URL; skipped unless ``COPYCAST_TEST_NETWORK=1``."""

from __future__ import annotations

import os

import pytest

from copycast.adapters.engine.ytdlp import YtDlpEngine
from copycast.application.ports import CancelToken, NullEngineLog
from copycast.domain.enums import ListingOrder

pytestmark = pytest.mark.network

PINNED_PLAYLIST = "https://www.youtube.com/playlist?list=PLbpi6ZahtOH6Blw3RGYpWkSByi_T7Rygb"
"""A short public YouTube playlist (the yt-dlp project uses it in its own tests)."""


@pytest.fixture(autouse=True)
def _needs_network() -> None:
    if os.environ.get("COPYCAST_TEST_NETWORK") != "1":
        pytest.skip("set COPYCAST_TEST_NETWORK=1 to run network tests")


def test_flat_extraction_of_a_public_playlist() -> None:
    listing = YtDlpEngine().list_source(PINNED_PLAYLIST, {}, CancelToken(), NullEngineLog())
    assert listing.service == "YouTube"
    assert listing.listing_order is ListingOrder.oldest_first
    assert listing.items, "the playlist should list at least one video"
    first = listing.items[0]
    assert first.source_key.startswith("Youtube:")
    assert first.source_number == 1
    assert first.source_url and "youtube.com/watch" in first.source_url
    assert listing.raw is not None and listing.raw.get("_type") == "playlist"
