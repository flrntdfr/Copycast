"""The fetch guard inside yt-dlp: every connection the Engine opens goes to a checked address.

yt-dlp runs in process with its own HTTP stack, so the guard of Copycast's httpx
client (``adapters.sources.guard``) never sees its requests: the redirects it
follows, the API calls and manifests an extractor makes, fragments, formats and
thumbnails. :func:`install` puts the same check on each place yt-dlp opens a TCP
connection: the host is resolved once, every answer must pass the installed policy
(``guard.checked_addresses``), and the socket connects to a validated address
literal, so neither a redirect to an internal host nor a DNS answer that changes
after a check (rebinding) reaches one. TLS still verifies, and SNI and ``Host``
still name, the URL's host: both come from the request, not the socket.

yt-dlp's built-in request handlers connect in two ways, and both are covered:

- urllib and websockets call ``create_connection`` from their own module namespace
  (for a SOCKS proxy too, whose address is then the one judged): the name is
  rebound to :func:`guarded_create_connection`;
- requests connects through urllib3 pools: the pool managers of its adapter get
  pools whose connections resolve through the guard (behind an HTTP proxy the
  peer judged is the proxy).

Behind a proxy (``proxy`` in ``[engine.options]``, or ``HTTP(S)_PROXY`` from the
environment, which yt-dlp reads when ``proxy`` is unset) the TCP peer is the proxy,
so the proxy is judged as the peer and every destination is judged on its own
before anything is sent: each request and redirect hop of the requests and urllib
handlers through an HTTP proxy (:func:`judge_proxied_url`), and every SOCKS
connection of all three (``create_socks_proxy_socket`` rebound). The proxy resolves
the name itself, so the destination's address cannot be pinned: a name that
resolves here is judged by what it resolves to here, and one that does not resolve
here passes (the proxy's own network decides what it reaches).

A handler that connects any other way (curl_cffi impersonation, or one a later
yt-dlp adds) is unregistered, and ``tests/unit/engine/test_netguard.py`` fails
when the set changes. Processes yt-dlp may start to download (ffmpeg as the
downloader of a stream it cannot fetch natively) are outside this guard.

A refusal surfaces inside yt-dlp as :class:`GuardRefusal`, an ``OSError`` every
handler turns into its usual network error; it carries the
:class:`DestinationNotAllowed`, which ``errors.destination_refusal`` finds in the
exception chain. Inside :func:`recording_refusals` every refusal of the calling
thread is also kept, for the listings whose errors yt-dlp only logs.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import urllib.parse
from collections.abc import Generator
from contextvars import ContextVar
from importlib import import_module
from typing import Any, Final, cast

from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError

from copycast.adapters.sources.guard import checked_addresses
from copycast.domain.destinations import IPAddress
from copycast.domain.exceptions import DestinationNotAllowed

GUARDED_HANDLERS: Final = frozenset({"Urllib", "Requests", "Websockets"})
"""The ``RH_KEY`` of every yt-dlp request handler whose connections pass the guard."""

SOCKS_SCHEMES: Final = ("socks4", "socks4a", "socks5", "socks5h")
DEFAULT_PORTS: Final = {"http": 80, "https": 443, "ws": 80, "wss": 443}
_UNSET: Final = object()
"""No timeout given: yt-dlp's own default (the socket module's) applies."""
_refusals: ContextVar[list[DestinationNotAllowed] | None] = ContextVar(
    "engine_refusals", default=None
)
_install_lock = threading.Lock()
_installed = False


class GuardRefusal(OSError):
    """The fetch guard refused a connection yt-dlp was about to open."""

    def __init__(self, refusal: DestinationNotAllowed) -> None:
        super().__init__(str(refusal))
        self.refusal = refusal


def validated(host: str, port: int) -> list[IPAddress]:
    """``guard.checked_addresses``, a refusal raised as :class:`GuardRefusal` and recorded."""
    try:
        return checked_addresses(host, port)
    except DestinationNotAllowed as exc:
        seen = _refusals.get()
        if seen is not None:
            seen.append(exc)
        raise GuardRefusal(exc) from exc


@contextlib.contextmanager
def recording_refusals() -> Generator[list[DestinationNotAllowed]]:
    """Collect the refusals of the connections this thread opens inside the block."""
    seen: list[DestinationNotAllowed] = []
    token = _refusals.set(seen)
    try:
        yield seen
    finally:
        _refusals.reset(token)


def guarded_create_connection(
    address: tuple[str, int],
    timeout: object = _UNSET,
    source_address: tuple[str, int] | None = None,
    **kwargs: Any,
) -> socket.socket:
    """yt-dlp's ``create_connection``, connecting only to validated address literals.

    ``kwargs`` carries yt-dlp's ``_create_socket_func`` (a SOCKS proxy's socket);
    a literal needs no name resolution, so the name is never resolved twice.
    """
    create_connection = _yt_dlp_module("_helper").create_connection
    if timeout is not _UNSET:
        kwargs["timeout"] = timeout
    host, port = address
    failure: OSError | None = None
    for ip in validated(str(host), int(port)):
        try:
            return cast(
                socket.socket,
                create_connection((str(ip), port), source_address=source_address, **kwargs),
            )
        except OSError as exc:
            failure = exc
    assert failure is not None
    raise failure


def judge_destination(host: str, port: int) -> None:
    """Refuse a destination a proxy is about to reach on the Engine's behalf.

    A name that does not resolve here passes: only the proxy resolves it, and nothing on
    this side could pin what it connects to.
    """
    try:
        validated(host, port)
    except GuardRefusal:
        raise
    except OSError:
        return


def judge_proxied_url(url: str) -> None:
    """:func:`judge_destination` for the host and port of ``url``."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return  # the handler refuses it as no URL
    if parts.hostname:
        judge_destination(parts.hostname, port or DEFAULT_PORTS.get(parts.scheme.lower(), 80))


def is_socks(proxy: str) -> bool:
    return urllib.parse.urlsplit(proxy).scheme.lower() in SOCKS_SCHEMES


def guarded_create_socks_proxy_socket(dest_addr: tuple[str, int], *args: Any) -> Any:
    """yt-dlp's ``create_socks_proxy_socket``: the destination judged before the proxy
    hears of it (the proxy itself was judged as the peer by
    :func:`guarded_create_connection`)."""
    host, port = dest_addr
    judge_destination(str(host), int(port))
    return _yt_dlp_module("_helper").create_socks_proxy_socket(dest_addr, *args)


class GuardedHTTPConnection(HTTPConnection):
    """urllib3's connection, connecting only to the addresses the guard validated."""

    def _new_conn(self) -> socket.socket:
        name = self._dns_host
        try:
            addresses = validated(name, self.port)
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except OSError as exc:
            raise NewConnectionError(self, f"Failed to establish a new connection: {exc}") from exc
        failure: ConnectTimeoutError | None = None
        for address in addresses:
            self._dns_host = str(address)
            try:
                return super()._new_conn()
            except ConnectTimeoutError as exc:  # NewConnectionError included
                failure = exc
            finally:
                self._dns_host = name
        assert failure is not None
        raise failure


class GuardedHTTPSConnection(GuardedHTTPConnection, HTTPSConnection):
    pass


# urllib3 types ConnectionCls as its connection Protocols, which its own classes match
# only nominally (a Final attribute against a mutable protocol member).
class GuardedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = GuardedHTTPConnection  # pyright: ignore[reportAssignmentType]


class GuardedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = GuardedHTTPSConnection  # pyright: ignore[reportAssignmentType]


GUARDED_POOLS: Final[dict[str, type[HTTPConnectionPool]]] = {
    "http": GuardedHTTPConnectionPool,
    "https": GuardedHTTPSConnectionPool,
}


def install() -> None:
    """Guard every connection yt-dlp opens from now on, in this process (idempotent)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        handlers = cast(dict[str, Any], _yt_dlp_module("common")._REQUEST_HANDLERS)
        for key in list(handlers):
            if key not in GUARDED_HANDLERS:
                del handlers[key]
        for name in ("_urllib", "_websockets", "_requests"):
            module = _optional_module(name)
            if module is not None:
                module.create_connection = guarded_create_connection
                module.create_socks_proxy_socket = guarded_create_socks_proxy_socket
        requests_module = _optional_module("_requests")
        if requests_module is not None:
            _guard_requests_adapter(requests_module.RequestsHTTPAdapter)
        urllib_module = _optional_module("_urllib")
        if urllib_module is not None:
            _guard_urllib_proxy_handler(urllib_module.ProxyHandler)
        _installed = True


def _guard_requests_adapter(adapter: Any) -> None:
    init_poolmanager = adapter.init_poolmanager
    proxy_manager_for = adapter.proxy_manager_for

    def guarded_init_poolmanager(self: Any, *args: Any, **kwargs: Any) -> None:
        init_poolmanager(self, *args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = GUARDED_POOLS

    def guarded_proxy_manager_for(self: Any, proxy: str, **kwargs: Any) -> Any:
        manager = proxy_manager_for(self, proxy, **kwargs)
        # A SOCKS proxy has pools of its own; they connect through create_connection.
        if not proxy.lower().startswith("socks"):
            manager.pool_classes_by_scheme = GUARDED_POOLS
        return manager

    get_connection = adapter.get_connection_with_tls_context

    def guarded_get_connection(
        self: Any, request: Any, verify: Any, proxies: Any = None, cert: Any = None
    ) -> Any:
        # requests asks for a connection once per request and per redirect hop.
        proxy = _select_proxy(request.url, proxies)
        if proxy and not is_socks(proxy):
            try:
                judge_proxied_url(request.url)
            except GuardRefusal as exc:
                requests_exceptions = import_module("requests.exceptions")
                raise requests_exceptions.ConnectionError(str(exc), request=request) from exc
        return get_connection(self, request, verify, proxies=proxies, cert=cert)

    adapter.init_poolmanager = guarded_init_poolmanager
    adapter.proxy_manager_for = guarded_proxy_manager_for
    adapter.get_connection_with_tls_context = guarded_get_connection


def _guard_urllib_proxy_handler(handler: Any) -> None:
    proxy_open = handler.proxy_open

    def guarded_proxy_open(self: Any, req: Any) -> Any:
        # urllib opens every request and redirect hop through this handler first.
        url = req.get_full_url()
        proxy = _select_proxy(url, self.proxies)
        if proxy and not is_socks(proxy):
            judge_proxied_url(url)
        return proxy_open(self, req)

    handler.proxy_open = guarded_proxy_open


def _select_proxy(url: str, proxies: Any) -> str | None:
    """The proxy yt-dlp uses for ``url`` (``no_proxy`` and scheme rules applied)."""
    if not proxies:
        return None
    networking = import_module("yt_dlp.utils.networking")
    return cast("str | None", networking.select_proxy(url, proxies))


def _yt_dlp_module(name: str) -> Any:
    return import_module(f"yt_dlp.networking.{name}")


def _optional_module(name: str) -> Any | None:
    """A handler module of yt-dlp, or None when its dependency is not installed."""
    try:
        return _yt_dlp_module(name)
    except ImportError:
        return None


__all__ = [
    "GUARDED_HANDLERS",
    "GUARDED_POOLS",
    "GuardRefusal",
    "GuardedHTTPConnection",
    "GuardedHTTPSConnection",
    "guarded_create_connection",
    "guarded_create_socks_proxy_socket",
    "install",
    "judge_destination",
    "judge_proxied_url",
    "recording_refusals",
    "validated",
]
