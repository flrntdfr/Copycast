"""The probe cache is kept per owner: one User's probe never serves another (§1.15)."""

from __future__ import annotations

from copycast.adapters.sources.probe import ProbeCache, ProbeEntry, probe, rss_candidate
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine
from tests.support.origin import Origin

URL = "https://podcast.example/feed.xml"


def _entry(url: str = URL) -> ProbeEntry:
    return ProbeEntry(rss_candidate(url, listing(1)), listing(1))


def test_an_entry_reaches_only_its_owner_by_token_and_by_url() -> None:
    cache = ProbeCache()
    entry = _entry()
    token = entry.candidate.candidate_token
    cache.put(entry, owner="alice")

    assert cache.get_by_token(token, owner="alice") is entry
    assert cache.get_by_url(URL, owner="alice") is entry
    assert cache.get(owner="alice", token=token) is entry
    assert cache.get(owner="alice", url=URL + "/") is entry

    assert cache.get_by_token(token, owner="bob") is None
    assert cache.get_by_url(URL, owner="bob") is None
    assert cache.get(owner="bob", token=token, url=URL) is None
    assert cache.get(owner="", token=token, url=URL) is None


def test_two_owners_keep_their_own_entry_for_one_url() -> None:
    cache = ProbeCache()
    alices, bobs = _entry(), _entry()
    cache.put(alices, owner="alice")
    cache.put(bobs, owner="bob")
    assert cache.get_by_url(URL, owner="alice") is alices
    assert cache.get_by_url(URL, owner="bob") is bobs
    # Each token belongs to its owner only, even with the other's URL entry present.
    assert cache.get_by_token(bobs.candidate.candidate_token, owner="alice") is None
    assert cache.get(owner="alice", token=bobs.candidate.candidate_token, url=URL) is alices
    cache.clear()
    assert len(cache) == 0


def test_probe_files_its_entries_under_the_owner(origin: Origin, engine: FakeEngine) -> None:
    cache = ProbeCache()
    url = origin.url_for("/rss/itunes_podcast20.xml")
    result = probe(url, engine=engine, cache=cache, owner="alice")
    token = result.candidates[0].candidate_token
    assert cache.get(owner="alice", token=token) is not None
    assert cache.get(owner="bob", token=token, url=url) is None


def test_an_owner_less_probe_reaches_no_user(origin: Origin, engine: FakeEngine) -> None:
    cache = ProbeCache()
    url = origin.url_for("/rss/itunes_podcast20.xml")
    token = probe(url, engine=engine, cache=cache).candidates[0].candidate_token
    assert cache.get(owner="", token=token) is not None
    assert cache.get(owner="copycast", token=token, url=url) is None
