"""The SSE envelope: the Owner prefix of NOTIFY payloads and per-Principal delivery.

``serialize(event, owner=...)`` builds ``"{owner}\\n{json}"`` and checks the size of the
whole payload; the EventHub splits it and hands an event only to its Owner, Admins and
SYSTEM, except ``resync``, which every subscriber gets.
"""

from __future__ import annotations

import pytest

from copycast.adapters.api.events import Envelope, EventHub, Subscription, split_payload
from copycast.application.events import (
    MAX_PAYLOAD_BYTES,
    EventTooLarge,
    FeedEvent,
    ResyncEvent,
    parse,
    serialize,
)
from copycast.domain.enums import PrincipalVia
from copycast.domain.users import Principal

ALICE = Principal(username="alice", admin=False, via=PrincipalVia.session)
BOB = Principal(username="bob", admin=False, via=PrincipalVia.session)
ROOT = Principal(username="root", admin=True, via=PrincipalVia.session)
ROOT_KEY = Principal(username="root", admin=True, via=PrincipalVia.api_key)


def feed_event(feed_id: str = "f1", reason: str = "updated") -> FeedEvent:
    return FeedEvent(feed_id=feed_id, revision=1, reason=reason)


async def feed_ids(subscription: Subscription) -> list[str]:
    """The feed ids of everything queued for ``subscription`` (``""`` for a resync)."""
    seen: list[str] = []
    while (envelope := await subscription.next(0.01)) is not None:
        seen.append(getattr(envelope.event, "feed_id", ""))
    return seen


def hub() -> EventHub:
    """A hub that never connects: ``publish`` and ``_handle_payload`` drive it."""
    return EventHub("postgresql://unused/none", buffer_size=10, queue_size=10)


# --------------------------------------------------------------------------- the codec


def test_serialize_prefixes_the_owner_and_keeps_the_data_line_bare() -> None:
    event = feed_event()
    bare = serialize(event)
    assert bare.startswith("{") and "\n" not in bare
    assert serialize(event, owner="bob") == f"bob\n{bare}"
    assert split_payload(serialize(event, owner="bob")) == ("bob", bare)
    assert split_payload(bare) == (None, bare)
    assert split_payload(f"\n{bare}") == (None, bare)
    assert parse(split_payload(serialize(event, owner="bob"))[1]) == event


def test_a_newline_inside_the_data_never_splits_the_payload() -> None:
    event = feed_event(reason="two\nlines")
    owner, body = split_payload(serialize(event, owner="alice"))
    assert owner == "alice" and parse(body) == event


def _event_of_size(size: int) -> FeedEvent:
    """A FeedEvent whose bare serialization is exactly ``size`` bytes."""
    base = len(serialize(feed_event(reason="")).encode())
    return feed_event(reason="x" * (size - base))


def test_the_size_limit_applies_to_the_prefixed_payload() -> None:
    event = _event_of_size(7990)
    assert len(serialize(event).encode()) == 7990
    owner = "a-long-owner-name"  # 17 bytes + the newline cross the 8000-byte limit
    assert 7990 + len(owner) + 1 >= MAX_PAYLOAD_BYTES
    with pytest.raises(EventTooLarge, match="limit is 7999"):
        serialize(event, owner=owner)
    assert serialize(event, owner="bob")  # 7994 bytes still fit


# --------------------------------------------------------------------------- delivery


def test_an_envelope_reaches_its_owner_admins_and_system_only() -> None:
    bobs = Envelope(id=1, event=feed_event(), owner="bob")
    assert bobs.visible_to(BOB)
    assert not bobs.visible_to(ALICE)
    assert bobs.visible_to(ROOT) and bobs.visible_to(ROOT_KEY)
    assert bobs.visible_to(Principal.SYSTEM)
    ownerless = Envelope(id=2, event=feed_event(), owner=None)
    assert ownerless.visible_to(ROOT) and not ownerless.visible_to(ALICE)
    resync = Envelope(id=3, event=ResyncEvent(reason="listen-reconnected"))
    assert resync.visible_to(ALICE) and resync.visible_to(BOB)


async def test_the_hub_routes_notify_payloads_by_owner() -> None:
    events = hub()
    with (
        events.subscribe(ALICE) as alice,
        events.subscribe(BOB) as bob,
        events.subscribe(ROOT) as root,
        events.subscribe() as system,
    ):
        events._handle_payload(serialize(feed_event("bobs"), owner="bob"))
        events._handle_payload(serialize(feed_event("system")))
        events._handle_payload(serialize(feed_event("alices"), owner="alice"))

        assert await feed_ids(alice) == ["alices"]
        assert await feed_ids(bob) == ["bobs"]
        assert await feed_ids(root) == ["bobs", "system", "alices"]
        assert await feed_ids(system) == ["bobs", "system", "alices"]


async def test_a_local_resync_reaches_a_member() -> None:
    events = hub()
    with events.subscribe(ALICE) as alice:
        events.publish(ResyncEvent(reason="listen-reconnected"))
        envelope = await alice.next(0.1)
    assert envelope is not None and envelope.is_resync


async def test_the_replay_gap_resync_reaches_a_member_but_not_others_events() -> None:
    events = hub()
    events._handle_payload(serialize(feed_event("bobs"), owner="bob"))
    events._handle_payload(serialize(feed_event("alices"), owner="alice"))
    with events.subscribe(ALICE, last_event_id=999) as gap:
        first = await gap.next(0.1)
        assert first is not None and first.is_resync
        assert "bobs" not in await feed_ids(gap)
    with events.subscribe(ALICE, last_event_id=0) as replay:
        assert await feed_ids(replay) == ["alices"]


async def test_a_feed_filter_still_lets_resync_through_and_never_widens() -> None:
    events = hub()
    with events.subscribe(ALICE, feed_id="bobs") as alice:
        events._handle_payload(serialize(feed_event("bobs"), owner="bob"))
        events.publish(ResyncEvent())
        envelope = await alice.next(0.1)
        assert envelope is not None and envelope.is_resync
        assert await alice.next(0.01) is None


def test_an_invalid_payload_after_the_prefix_is_dropped() -> None:
    events = hub()
    events._handle_payload("bob\nnot json")
    assert events.last_id == 0
