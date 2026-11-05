"""The fetch guard inside yt-dlp: which connection points are patched, and what they do."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from importlib import import_module
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import pytest
from urllib3.exceptions import NameResolutionError, NewConnectionError

from copycast.adapters.engine import netguard
from copycast.adapters.engine.errors import destination_refusal
from copycast.adapters.engine.netguard import (
    GUARDED_HANDLERS,
    GuardedHTTPConnection,
    GuardRefusal,
    guarded_create_connection,
    recording_refusals,
)
from copycast.adapters.sources.guard import install_policy
from copycast.domain.destinations import DestinationPolicy, IPAddress
from copycast.domain.exceptions import DestinationNotAllowed
from tests.support.origin import Origin

PUBLIC = "93.184.216.34"
KNOWN_HANDLER_MODULES = {"_urllib", "_requests", "_websockets", "_curlcffi"}
"""yt-dlp's request handler modules at the pinned version; guarded or unregistered."""


@pytest.fixture(autouse=True)
def installed() -> None:
    netguard.install()  # what YtDlpEngine() does


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """What the guard's resolver answers per name (``tests.support.dns`` for the rest)."""
    table: dict[str, list[str]] = {}
    fallback = import_module("copycast.adapters.sources.guard").resolve

    def resolve(host: str, port: int) -> list[IPAddress]:
        if host in table:
            return [ip_address(a) for a in table[host]]
        return fallback(host, port)

    monkeypatch.setattr("copycast.adapters.sources.guard.resolve", resolve)
    return table


@pytest.fixture
def no_exemptions() -> Iterator[None]:
    previous = install_policy(DestinationPolicy())
    try:
        yield
    finally:
        install_policy(previous)


@pytest.fixture
def connects(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """yt-dlp's own ``create_connection`` replaced by a recorder: no socket is opened."""
    calls: list[tuple[Any, ...]] = []

    def create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        calls.append((address, kwargs))
        if address[0] == "203.0.113.99":
            raise ConnectionRefusedError("refused")
        return "socket"

    monkeypatch.setattr(
        import_module("yt_dlp.networking._helper"), "create_connection", create_connection
    )
    return calls


def test_only_guarded_request_handlers_stay_registered() -> None:
    handlers = import_module("yt_dlp.networking.common")._REQUEST_HANDLERS
    assert set(handlers) <= GUARDED_HANDLERS
    assert {"Urllib", "Requests"} <= set(handlers)


def test_every_handler_module_of_the_engine_is_known() -> None:
    """A yt-dlp bump that adds a request handler fails here: it would connect unguarded."""
    networking = Path(import_module("yt_dlp.networking").__file__ or "").parent
    modules = {p.stem for p in networking.glob("_*.py")} - {"__init__", "_helper"}
    assert modules == KNOWN_HANDLER_MODULES


def test_the_handlers_connect_through_the_guard() -> None:
    for name in ("_urllib", "_requests", "_websockets"):
        assert import_module(f"yt_dlp.networking.{name}").create_connection is (
            guarded_create_connection
        )
    adapter = import_module("yt_dlp.networking._requests").RequestsHTTPAdapter()
    assert adapter.poolmanager.pool_classes_by_scheme is netguard.GUARDED_POOLS
    assert adapter.proxy_manager_for("http://proxy.example:3128").pool_classes_by_scheme is (
        netguard.GUARDED_POOLS
    )


def test_install_is_idempotent() -> None:
    adapter = import_module("yt_dlp.networking._requests").RequestsHTTPAdapter
    before = adapter.init_poolmanager
    netguard.install()
    assert adapter.init_poolmanager is before


@pytest.mark.usefixtures("no_exemptions")
def test_create_connection_refuses_before_connecting_and_records_it(
    answers: dict[str, list[str]], connects: list[tuple[Any, ...]]
) -> None:
    answers["metadata.internal"] = [PUBLIC, "169.254.169.254"]
    with recording_refusals() as refused, pytest.raises(GuardRefusal) as raised:
        guarded_create_connection(("metadata.internal", 80), 5.0)
    assert connects == []
    assert isinstance(raised.value, OSError)
    assert refused == [raised.value.refusal]
    assert destination_refusal(RuntimeError("wrapped")) is None
    try:
        raise RuntimeError("Unable to download webpage") from raised.value
    except RuntimeError as wrapped:
        assert destination_refusal(wrapped) is raised.value.refusal


@pytest.mark.usefixtures("no_exemptions")
def test_create_connection_connects_to_the_validated_literal(
    answers: dict[str, list[str]], connects: list[tuple[Any, ...]]
) -> None:
    """The name is resolved once; yt-dlp connects to the address the guard accepted."""
    answers["cdn.example"] = ["203.0.113.99", PUBLIC]  # one documentation-range answer
    with pytest.raises(GuardRefusal):
        guarded_create_connection(("cdn.example", 443))
    answers["cdn.example"] = ["2606:4700::1", PUBLIC]
    sock = guarded_create_connection(("cdn.example", 443), 5.0, None, _create_socket_func=print)
    assert sock == "socket"
    assert connects == [
        (
            ("2606:4700::1", 443),
            {"source_address": None, "_create_socket_func": print, "timeout": 5.0},
        )
    ]


def test_create_connection_tries_the_next_validated_address(
    answers: dict[str, list[str]], connects: list[tuple[Any, ...]]
) -> None:
    previous = install_policy(DestinationPolicy.from_entries(["203.0.113.0/24"]))
    try:
        answers["cdn.example"] = ["203.0.113.99", "203.0.113.7"]
        assert guarded_create_connection(("cdn.example", 80)) == "socket"
        assert [c[0] for c in connects] == [("203.0.113.99", 80), ("203.0.113.7", 80)]
        answers["cdn.example"] = ["203.0.113.99"]
        with pytest.raises(ConnectionRefusedError):
            guarded_create_connection(("cdn.example", 80))
    finally:
        install_policy(previous)


def test_a_refusal_outside_a_recording_block_is_not_kept(
    answers: dict[str, list[str]], connects: list[tuple[Any, ...]], no_exemptions: None
) -> None:
    with pytest.raises(GuardRefusal):
        guarded_create_connection(("127.0.0.1", 80))
    with recording_refusals() as refused:
        pass
    assert refused == []


def test_the_urllib3_connection_is_pinned_to_the_checked_address(
    origin: Origin, answers: dict[str, list[str]]
) -> None:
    """requests' connections resolve through the guard; Host still names the URL's host."""
    answers["named.example"] = ["127.0.0.1"]
    previous = install_policy(DestinationPolicy.from_entries(["127.0.0.0/8"]))
    try:
        conn = GuardedHTTPConnection("named.example", origin.port)
        conn.request("GET", "/media/tiny.jpg")
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        install_policy(previous)
    assert conn._dns_host == "named.example"  # pyright: ignore[reportPrivateUsage]
    assert origin.requests[0].headers["host"] == f"named.example:{origin.port}"


@pytest.mark.usefixtures("no_exemptions")
def test_the_urllib3_connection_refuses_an_internal_answer(
    origin: Origin, answers: dict[str, list[str]]
) -> None:
    answers["named.example"] = ["127.0.0.1"]
    conn = GuardedHTTPConnection("named.example", origin.port)
    with pytest.raises(NewConnectionError) as raised:
        conn.request("GET", "/media/tiny.jpg")
    assert isinstance(raised.value.__cause__, GuardRefusal)
    assert isinstance(destination_refusal(raised.value), DestinationNotAllowed)
    assert origin.requests == []


def test_the_urllib3_connection_reports_a_name_that_does_not_resolve() -> None:
    conn = GuardedHTTPConnection("nowhere.invalid", 80)
    with pytest.raises(NameResolutionError):
        conn.connect()


def test_socket_errors_keep_their_meaning(answers: dict[str, list[str]]) -> None:
    """A name that does not resolve is a ``gaierror``, not a refusal."""
    with pytest.raises(socket.gaierror):
        guarded_create_connection(("nowhere.invalid", 80))
