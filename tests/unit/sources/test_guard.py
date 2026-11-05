"""The fetch guard: address checks at connect time, redirects, rebinding, the Engine's URLs."""

from __future__ import annotations

import ssl
from collections.abc import Iterable, Iterator
from ipaddress import ip_address
from typing import Any

import httpcore
import httpx
import pytest

from copycast.adapters.sources.guard import (
    GuardedBackend,
    GuardedTransport,
    check_engine_url,
    check_url,
    checked_addresses,
    current_policy,
    install_policy,
)
from copycast.adapters.sources.http import (
    SourceError,
    content_type_of,
    create_client,
    fetch,
)
from copycast.domain.destinations import DestinationPolicy, IPAddress
from copycast.domain.exceptions import DestinationNotAllowed
from tests.support.origin import Origin

PUBLIC = "93.184.216.34"
OK_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok"


@pytest.fixture
def no_exemptions() -> Iterator[DestinationPolicy]:
    """The production default: nothing exempt (the suite's own policy comes back afterwards)."""
    policy = DestinationPolicy()
    previous = install_policy(policy)
    try:
        yield policy
    finally:
        install_policy(previous)


def fixed(*answers: list[str]) -> Any:
    """A resolver returning ``answers`` in turn, one per lookup, and recording the lookups."""
    queue = [[ip_address(a) for a in answer] for answer in answers]
    lookups: list[tuple[str, int]] = []

    def resolve(host: str, port: int) -> list[IPAddress]:
        lookups.append((host, port))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    resolve.lookups = lookups  # type: ignore[attr-defined]
    return resolve


class RecordingStream(httpcore.MockStream):
    """A canned HTTP answer that keeps what the client wrote and the SNI it asked for."""

    def __init__(self, buffer: list[bytes]) -> None:
        super().__init__(buffer)
        self.written = b""
        self.server_hostname: str | None = None

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written += buffer

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        self.server_hostname = server_hostname
        return self


class RecordingBackend(GuardedBackend):
    """A guarded backend whose sockets are canned streams: no packet leaves the test."""

    def __init__(self, *, refuse: Iterable[str] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.opened: list[tuple[str, int]] = []
        self.streams: list[RecordingStream] = []
        self._refuse = set(refuse)

    def open(
        self,
        address: str,
        port: int,
        timeout: float | None,
        local_address: str | None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None,
    ) -> httpcore.NetworkStream:
        self.opened.append((address, port))
        if address in self._refuse:
            raise httpcore.ConnectError(f"connection to {address} refused")
        stream = RecordingStream([OK_RESPONSE])
        self.streams.append(stream)
        return stream


def client_over(backend: httpcore.NetworkBackend) -> httpx.Client:
    return httpx.Client(transport=GuardedTransport(backend=backend), follow_redirects=True)


# --------------------------------------------------------------------------- the policy


def test_install_policy_returns_the_previous_one() -> None:
    mine = DestinationPolicy.from_entries(["nas.lan"])
    previous = install_policy(mine)
    try:
        assert current_policy() is mine
    finally:
        assert install_policy(previous) is mine


def test_checked_addresses_refuses_when_any_answer_is_internal() -> None:
    resolve = fixed([PUBLIC, "10.0.0.7"])
    with pytest.raises(DestinationNotAllowed) as refused:
        checked_addresses("mixed.example", 443, policy=DestinationPolicy(), resolver=resolve)
    assert refused.value.target == "mixed.example:443"
    assert "private address" in refused.value.reason
    assert "security.allow_private_hosts" in refused.value.reason
    assert "10.0.0.7" not in str(refused.value)


def test_checked_addresses_accepts_an_exempt_name_whatever_it_resolves_to() -> None:
    policy = DestinationPolicy.from_entries(["fixtures"])
    addresses = checked_addresses("Fixtures", 8000, policy=policy, resolver=fixed(["172.18.0.4"]))
    assert addresses == [ip_address("172.18.0.4")]


def test_checked_addresses_accepts_an_exempt_network() -> None:
    policy = DestinationPolicy.from_entries(["172.16.0.0/12"])
    resolve = fixed(["172.18.0.4"])
    assert checked_addresses("nas", 80, policy=policy, resolver=resolve) == [
        ip_address("172.18.0.4")
    ]
    with pytest.raises(DestinationNotAllowed):
        checked_addresses("nas", 80, policy=policy, resolver=fixed(["127.0.0.1"]))


def test_checked_addresses_raises_oserror_for_an_empty_answer() -> None:
    with pytest.raises(OSError, match="no IP address"):
        checked_addresses("void.example", 80, policy=DestinationPolicy(), resolver=lambda h, p: [])


# --------------------------------------------------------------------------- URLs ahead of a fetch


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/feed.xml",
        "gopher://127.0.0.1:6379/_INFO",
        "seaweedfs:8333/copycast/feeds",
        "//127.0.0.1/feed.xml",
        "javascript:alert(1)",
    ],
)
def test_check_url_refuses_other_schemes(url: str) -> None:
    with pytest.raises(DestinationNotAllowed, match="only http and https URLs are fetched"):
        check_url(url, policy=DestinationPolicy(), resolver=fixed([PUBLIC]))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8333/copycast/",
        "http://seaweedfs:8888/",
        "http://169.254.169.254/latest/meta-data/",
        "https://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://user:pass@internal.example/",
        "http://public.example@internal.example/",
    ],
)
def test_check_url_refuses_internal_hosts(url: str) -> None:
    host_answers = {
        "127.0.0.1": "127.0.0.1",
        "seaweedfs": "172.18.0.3",
        "169.254.169.254": "169.254.169.254",
        "::1": "::1",
        "::ffff:127.0.0.1": "::ffff:127.0.0.1",
        "internal.example": "10.1.2.3",
    }

    def resolve(host: str, port: int) -> list[IPAddress]:
        return [ip_address(host_answers[host])]

    with pytest.raises(DestinationNotAllowed) as refused:
        check_url(url, policy=DestinationPolicy(), resolver=resolve)
    assert "pass" not in str(refused.value) and "user" not in str(refused.value)


@pytest.mark.parametrize("url", ["http://127.1/", "http://2130706433/", "http://0x7f000001/"])
def test_check_url_resolves_numeric_shorthands_like_the_fetch_would(url: str) -> None:
    with pytest.raises(DestinationNotAllowed, match="loopback"):
        check_url(url, policy=DestinationPolicy())


def test_check_url_refuses_a_url_without_host_or_with_a_bad_port() -> None:
    with pytest.raises(DestinationNotAllowed, match="cannot be parsed"):
        check_url("http://[::1/feed.xml", policy=DestinationPolicy())
    with pytest.raises(DestinationNotAllowed, match="names no host"):
        check_url("http:///feed.xml", policy=DestinationPolicy())
    with pytest.raises(DestinationNotAllowed, match="port is invalid"):
        check_url("http://example.com:99999/", policy=DestinationPolicy(), resolver=fixed([PUBLIC]))


def test_check_url_passes_public_and_unresolvable_hosts() -> None:
    check_url("https://feeds.example.com/rss", policy=DestinationPolicy(), resolver=fixed([PUBLIC]))

    def unresolvable(host: str, port: int) -> list[IPAddress]:
        raise OSError("Name or service not known")

    check_url("https://nowhere.invalid/rss", policy=DestinationPolicy(), resolver=unresolvable)


def test_check_url_uses_the_default_port_of_the_scheme() -> None:
    resolve = fixed([PUBLIC])
    check_url("https://example.com/a", policy=DestinationPolicy(), resolver=resolve)
    check_url("http://example.com/a", policy=DestinationPolicy(), resolver=resolve)
    check_url("http://example.com:8080/a", policy=DestinationPolicy(), resolver=resolve)
    assert resolve.lookups == [("example.com", 443), ("example.com", 80), ("example.com", 8080)]


def test_check_engine_url_lets_search_keys_through_only() -> None:
    check_engine_url("ytsearch10:some podcast", policy=DestinationPolicy())
    check_engine_url("ytsearchdate5:news", policy=DestinationPolicy())
    with pytest.raises(DestinationNotAllowed):
        check_engine_url(":ytwatchlater", policy=DestinationPolicy())
    with pytest.raises(DestinationNotAllowed):
        check_engine_url("http://127.0.0.1/x", policy=DestinationPolicy())


# --------------------------------------------------------------------------- at connect time


def test_the_backend_connects_to_the_address_it_validated() -> None:
    """Host and TLS SNI still name the URL's host; the socket goes to the checked address."""
    resolve = fixed([PUBLIC])
    backend = RecordingBackend(policy=DestinationPolicy, resolver=resolve)
    with client_over(backend) as client:
        response = client.get("https://feeds.example.com/rss")
    assert response.status_code == 200 and response.text == "ok"
    assert backend.opened == [(PUBLIC, 443)]
    assert resolve.lookups == [("feeds.example.com", 443)]
    stream = backend.streams[0]
    assert stream.server_hostname == "feeds.example.com"
    assert b"Host: feeds.example.com\r\n" in stream.written


def test_rebinding_cannot_turn_a_checked_name_into_an_internal_address() -> None:
    """The resolver answers public first, then loopback: the second connection is refused."""
    resolve = fixed([PUBLIC], ["127.0.0.1"])
    backend = RecordingBackend(policy=DestinationPolicy, resolver=resolve)
    first = backend.connect_tcp("rebind.example", 80)
    assert isinstance(first, RecordingStream)
    with pytest.raises(DestinationNotAllowed, match="loopback"):
        backend.connect_tcp("rebind.example", 80)
    assert backend.opened == [(PUBLIC, 80)]


def test_rebinding_through_the_client_is_refused_on_the_new_connection() -> None:
    resolve = fixed([PUBLIC], ["169.254.169.254"])
    backend = RecordingBackend(policy=DestinationPolicy, resolver=resolve)
    with client_over(backend) as client:
        assert client.get("http://rebind.example/a").status_code == 200
    # A new client (new pool) must open a new connection, so it resolves again.
    with client_over(backend) as client, pytest.raises(DestinationNotAllowed, match="link-local"):
        client.get("http://rebind.example/b")
    assert backend.opened == [(PUBLIC, 80)]


def test_the_backend_tries_the_next_validated_address_when_one_fails() -> None:
    backend = RecordingBackend(
        refuse={"2606:4700::1"}, policy=DestinationPolicy, resolver=fixed(["2606:4700::1", PUBLIC])
    )
    with client_over(backend) as client:
        assert client.get("http://dual.example/").status_code == 200
    assert backend.opened == [("2606:4700::1", 80), (PUBLIC, 80)]


def test_the_backend_reports_the_last_connect_failure() -> None:
    backend = RecordingBackend(refuse={PUBLIC}, policy=DestinationPolicy, resolver=fixed([PUBLIC]))
    with pytest.raises(httpcore.ConnectError, match="refused"):
        backend.connect_tcp("down.example", 80)


def test_an_unresolvable_name_is_a_connect_error_not_a_refusal() -> None:
    def unresolvable(host: str, port: int) -> list[IPAddress]:
        raise OSError("Name or service not known")

    backend = RecordingBackend(policy=DestinationPolicy, resolver=unresolvable)
    with client_over(backend) as client, pytest.raises(httpx.ConnectError, match="cannot resolve"):
        client.get("http://nowhere.invalid/")
    assert backend.opened == []


def test_the_transport_refuses_other_schemes() -> None:
    transport = GuardedTransport(backend=RecordingBackend(resolver=fixed([PUBLIC])))
    with pytest.raises(DestinationNotAllowed, match="only http and https"):
        transport.handle_request(httpx.Request("GET", "ftp://example.com/feed.xml"))


def test_the_backend_reads_the_installed_policy_at_each_connection() -> None:
    backend = RecordingBackend(resolver=fixed(["10.0.0.9"]))
    previous = install_policy(DestinationPolicy.from_entries(["10.0.0.0/8"]))
    try:
        backend.connect_tcp("nas.lan", 80)
        install_policy(DestinationPolicy())
        with pytest.raises(DestinationNotAllowed):
            backend.connect_tcp("nas.lan", 80)
    finally:
        install_policy(previous)


# --------------------------------------------------------------------------- the shared client


@pytest.mark.usefixtures("no_exemptions")
def test_fetch_refuses_loopback_before_connecting(origin: Origin) -> None:
    with pytest.raises(DestinationNotAllowed, match=r"127\.0\.0\.1:\d+ .* loopback") as refused:
        fetch(origin.url_for("/media/tiny.jpg"), max_bytes=1_000_000)
    assert not isinstance(refused.value, SourceError)
    assert origin.requests == []


@pytest.mark.usefixtures("no_exemptions")
def test_content_type_of_does_not_hide_a_refusal(origin: Origin) -> None:
    with pytest.raises(DestinationNotAllowed):
        content_type_of(origin.url_for("/media/tiny.mp3"))
    assert origin.requests == []


def test_every_redirect_hop_is_checked(origin: Origin) -> None:
    """``localhost`` is exempt by name; its redirect to the bare loopback address is not."""
    previous = install_policy(DestinationPolicy.from_entries(["localhost"]))
    try:
        target = origin.url_for("/media/tiny.jpg")
        origin.script("/start", status=302, headers={"Location": target})
        start = f"http://localhost:{origin.port}/start"
        with pytest.raises(DestinationNotAllowed, match="loopback"):
            fetch(start, max_bytes=1_000_000)
        assert [r.path for r in origin.requests] == ["/start"]

        origin.script(
            "/start", status=302, headers={"Location": f"http://localhost:{origin.port}/ok"}
        )
        origin.script("/ok", body=b"fine", content_type="text/plain")
        assert fetch(start, max_bytes=1_000_000).body == b"fine"
    finally:
        install_policy(previous)


def test_a_redirect_to_another_scheme_is_refused(origin: Origin) -> None:
    origin.script("/start", status=302, headers={"Location": "file:///etc/passwd"})
    with pytest.raises(DestinationNotAllowed, match="only http and https"):
        fetch(origin.url_for("/start"), max_bytes=1_000_000)


def test_the_suite_policy_exempts_the_loopback_origin(origin: Origin) -> None:
    """The fake servers of the suite pass because ``[security]`` exempts them, as configured."""
    assert fetch(origin.url_for("/media/tiny.jpg"), max_bytes=1_000_000).status == 200


def test_create_client_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment proxy would connect on the guard's behalf, so the guarded client has none."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    with create_client() as client:
        assert isinstance(client._transport, GuardedTransport)  # pyright: ignore[reportPrivateUsage]
        assert client._mounts == {}  # pyright: ignore[reportPrivateUsage]
