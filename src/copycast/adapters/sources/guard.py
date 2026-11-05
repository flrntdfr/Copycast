"""The fetch guard: no Source-driven request reaches a loopback, private or internal address.

Every outbound request a Source influences (the probe, directory lookups, RSS
Refreshes, YouTube's Atom feed, Artwork and Attachments) goes through the httpx
client of ``adapters.sources.http``, whose transport is a :class:`GuardedTransport`.
Its network backend (:class:`GuardedBackend`) resolves the host itself, judges
every address the name resolves to (``domain.destinations``), and connects to one
of those validated addresses: the name is never resolved a second time, so a DNS
answer that changes between the check and the connection (rebinding) cannot slip
an internal address in. TLS still verifies, and SNI and ``Host`` still name, the
URL's host, because httpcore takes both from the request, not from the socket.
Redirects are followed by the client one hop at a time, and every hop to a new
origin opens a new connection through the same backend, so each hop is checked.

The Engine runs yt-dlp in process with its own HTTP stack: :func:`check_engine_url`
judges the URLs Copycast hands it (see ``adapters.engine.ytdlp``), not the requests
the Engine then makes on its own.

The policy is process-wide: :func:`install_policy` is called by the composition
root with ``[security] allow_private_hosts``; until then nothing is exempt. The
Object store client and the Feed cache are infrastructure and never pass here.
"""

from __future__ import annotations

import re
import socket
import threading
from collections.abc import Callable, Iterable
from ipaddress import ip_address
from typing import Final
from urllib.parse import urlsplit

import httpcore
import httpx

from copycast.domain.destinations import DestinationPolicy, IPAddress
from copycast.domain.exceptions import DestinationNotAllowed
from copycast.logging import get_logger

Resolver = Callable[[str, int], list[IPAddress]]
"""``(host, port) -> addresses``; raises ``OSError`` when the name does not resolve."""

ALLOWED_SCHEMES: Final = frozenset({"http", "https"})
DEFAULT_PORTS: Final = {"http": 80, "https": 443}
ENGINE_SEARCH_KEY: Final = re.compile(r"^ytsearch(?:date|all)?\d*:", re.IGNORECASE)
"""The Engine's search pseudo-URLs (``ytsearch10:query``) name no destination of their own."""

log = get_logger(__name__)

_policy = DestinationPolicy()
_policy_lock = threading.Lock()


def install_policy(policy: DestinationPolicy) -> DestinationPolicy:
    """Make ``policy`` the process's guard policy; returns the one it replaces."""
    global _policy
    with _policy_lock:
        previous, _policy = _policy, policy
    return previous


def current_policy() -> DestinationPolicy:
    with _policy_lock:
        return _policy


def resolve(host: str, port: int) -> list[IPAddress]:
    """Every address ``host`` resolves to (``getaddrinfo``), in its order, without repeats."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: dict[IPAddress, None] = {}
    for family, _type, _proto, _name, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            addresses[ip_address(str(sockaddr[0]))] = None
    if not addresses:
        raise OSError(f"{host} resolves to no IP address")
    return list(addresses)


def _target(host: str, port: int | None) -> str:
    shown = f"[{host}]" if ":" in host else host
    return f"{shown}:{port}" if port is not None else shown


def checked_addresses(
    host: str,
    port: int,
    *,
    policy: DestinationPolicy | None = None,
    resolver: Resolver = resolve,
) -> list[IPAddress]:
    """The addresses ``host`` resolves to, once every one passed the policy.

    Raises :class:`DestinationNotAllowed` when any of them is refused (one internal
    answer among public ones is enough), ``OSError`` when the name does not resolve.
    A host the policy names is accepted whatever it resolves to.
    """
    rules = policy if policy is not None else current_policy()
    addresses = resolver(host, port)
    if not addresses:
        raise OSError(f"{host} resolves to no IP address")
    if rules.allows_host(host):
        return addresses
    for address in addresses:
        reason = rules.refusal(address)
        if reason is not None:
            log.warning(
                "fetch.destination_refused",
                host=host,
                port=port,
                address=str(address),
                reason=reason,
            )
            raise DestinationNotAllowed(
                _target(host, port),
                f"it resolves to a {reason} address "
                "(an operator can exempt it with security.allow_private_hosts)",
            )
    return addresses


def check_url(
    url: str, *, policy: DestinationPolicy | None = None, resolver: Resolver = resolve
) -> None:
    """Refuse ``url`` unless it is http(s) and its host resolves to allowed addresses only.

    A name that does not resolve passes: there is nothing to judge, and the fetch
    that follows fails to resolve it the same way. This is a check ahead of a fetch
    made by someone else (the Engine), so it cannot pin the address that fetch
    connects to; the httpx transport below does.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise DestinationNotAllowed("a malformed URL", "the URL cannot be parsed") from None
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise DestinationNotAllowed(
            f"a {scheme}: URL" if scheme else "a URL without a scheme",
            "only http and https URLs are fetched",
        )
    host = parts.hostname
    if not host:
        raise DestinationNotAllowed("a URL without a host", "the URL names no host")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError:
        raise DestinationNotAllowed(_target(host, None), "the URL's port is invalid") from None
    try:
        checked_addresses(host, port, policy=policy, resolver=resolver)
    except OSError:
        return


def check_engine_url(
    url: str, *, policy: DestinationPolicy | None = None, resolver: Resolver = resolve
) -> None:
    """:func:`check_url`, letting the Engine's own search keys (``ytsearchN:``) through."""
    if ENGINE_SEARCH_KEY.match(url.strip()):
        return
    check_url(url, policy=policy, resolver=resolver)


class GuardedBackend(httpcore.SyncBackend):
    """httpcore's socket backend, connecting only to addresses the policy accepted.

    ``policy`` is read at every connection (the installed one by default), so a
    policy installed after the client was built applies to its next connection.
    """

    def __init__(
        self,
        *,
        policy: Callable[[], DestinationPolicy] = current_policy,
        resolver: Resolver = resolve,
    ) -> None:
        self._policy = policy
        self._resolver = resolver

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = checked_addresses(
                host, port, policy=self._policy(), resolver=self._resolver
            )
        except OSError as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}: {exc}") from exc
        failure: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            try:
                return self.open(str(address), port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc
        assert failure is not None
        raise failure

    def open(
        self,
        address: str,
        port: int,
        timeout: float | None,
        local_address: str | None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None,
    ) -> httpcore.NetworkStream:
        """Connect to one validated address literal (never a name)."""
        return super().connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class GuardedTransport(httpx.HTTPTransport):
    """httpx's transport over a :class:`GuardedBackend`; refuses every scheme but http(s).

    Passing a transport also keeps httpx from mounting the ``HTTP(S)_PROXY`` proxies
    of the environment, which would connect on the guard's behalf.
    """

    def __init__(
        self,
        *,
        backend: httpcore.NetworkBackend | None = None,
        limits: httpx.Limits | None = None,
    ) -> None:
        pool_limits = limits or httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0
        )
        ssl_context = httpx.create_ssl_context()
        super().__init__(verify=ssl_context, limits=pool_limits)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            max_connections=pool_limits.max_connections,
            max_keepalive_connections=pool_limits.max_keepalive_connections,
            keepalive_expiry=pool_limits.keepalive_expiry,
            network_backend=backend or GuardedBackend(),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        scheme = request.url.scheme
        if scheme not in ALLOWED_SCHEMES:
            raise DestinationNotAllowed(f"a {scheme}: URL", "only http and https URLs are fetched")
        return super().handle_request(request)


__all__ = [
    "ENGINE_SEARCH_KEY",
    "GuardedBackend",
    "GuardedTransport",
    "Resolver",
    "check_engine_url",
    "check_url",
    "checked_addresses",
    "current_policy",
    "install_policy",
    "resolve",
]
