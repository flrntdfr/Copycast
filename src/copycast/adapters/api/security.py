"""Pure ASGI browser defences: the cross-site request guard and the security headers.

:class:`CrossSiteGuardMiddleware` refuses unsafe requests a browser sends on behalf of
another site. Browsers attach a cached Basic pair (and cookies) to cross-site requests,
and with authentication off any page the operator visits could POST to a tailnet
Copycast; the guard runs whether authentication is on or off, ahead of it and of every
route (``/api``, ``/uploads``, ``/mcp`` alike). It also refuses any request, whatever its
method and headers, addressed to a host name that is not Copycast's: a page whose name the
attacker rebinds to Copycast's address is same-origin with itself, so only its ``Host``
gives it away (DNS rebinding). Over plain http a browser sends no Fetch Metadata and a
same-origin GET no ``Origin``, so the check cannot wait for either; only ``/healthz``
(container and Kubernetes probes) is left out.

:class:`SecurityHeadersMiddleware` sets ``nosniff`` and ``Referrer-Policy`` on every
response, and on everything but ``/feeds/`` and ``/art/`` (what podcast apps fetch) a
Content-Security-Policy, ``X-Frame-Options``, ``Cross-Origin-Opener-Policy`` and, when
``base_url`` is https, ``Strict-Transport-Security``. Podcast responses other than audio,
video and PDF get ``Content-Security-Policy: sandbox``, which podcast apps ignore: a mirrored
HTML transcript (or a feed's XML) opened in a browser runs no script on Copycast's
origin. A header a route already set wins.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from copycast.adapters.api.problems import build_problem
from copycast.logging import get_logger, redact_text

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SAME_ORIGIN_FETCH_SITES = frozenset({"same-origin", "none"})
"""``Sec-Fetch-Site`` values of a request the page itself (or the user, typing a URL or
following a bookmark) started; ``same-site`` and ``cross-site`` are refused."""
CROSS_SITE_SLUG = "cross-site-request"
UNKNOWN_HOST_SLUG = "unknown-host"
ANY_HOST = "*"
"""The ``COPYCAST_ALLOWED_HOSTS`` entry that turns the host check off."""
DEFAULT_PORTS = {"http": 80, "https": 443}
HOST_UNCHECKED_PATH = "/healthz"
"""Probes address the api by whatever name or address they have; its answers carry nothing."""

log = get_logger(__name__)


def normalized_origin(value: str) -> str | None:
    """``scheme://host[:port]`` lowercased with the default port dropped, else ``None``
    (``null``, a path, userinfo, a scheme other than http(s))."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in DEFAULT_PORTS or not host or parts.username is not None:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if ":" in host:
        host = f"[{host}]"
    suffix = f":{port}" if port is not None and port != DEFAULT_PORTS[scheme] else ""
    return f"{scheme}://{host}{suffix}"


def base_origin(base_url: str) -> str | None:
    """The origin of ``base_url``: its scheme and authority, whatever path it has (Copycast
    behind a proxy that strips a prefix)."""
    try:
        parts = urlsplit(base_url.strip())
    except ValueError:
        return None
    return normalized_origin(f"{parts.scheme}://{parts.netloc}")


def host_name(host: str) -> str | None:
    """The host name of a ``Host`` header, lowercased, without brackets, port or trailing
    dot; ``None`` when it is no host."""
    try:
        parts = urlsplit(f"//{host.strip()}")
        _ = parts.port
    except ValueError:
        return None
    if parts.username is not None or parts.path or parts.query or parts.fragment:
        return None
    name = (parts.hostname or "").rstrip(".")
    return name or None


def is_unbindable_host(name: str) -> bool:
    """A name no attacker can point at Copycast: an IP literal (a page can only be on it by
    being served from it) or ``localhost`` and its subdomains (browsers resolve them to
    loopback themselves). The Vite dev server and Kubernetes probes use these."""
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def is_host_unchecked(path: str) -> bool:
    return path == HOST_UNCHECKED_PATH or path.startswith(f"{HOST_UNCHECKED_PATH}/")


def foreign_host_refusal(headers: Headers, allowed: frozenset[str], path: str) -> str | None:
    """Why this request is refused for its ``Host``, or ``None`` when it may proceed.

    Every request is checked but those to ``/healthz``. A rebound page over plain http
    sends no ``Sec-Fetch-Site`` (browsers send Fetch Metadata to trustworthy origins only)
    and its same-origin GETs no ``Origin``, so neither header can decide who is checked.
    Clients that are no browser pass as long as they use ``base_url``'s name, an IP
    literal, ``localhost`` or a name of ``COPYCAST_ALLOWED_HOSTS`` (``allowed`` holds the
    first and the last).
    """
    if ANY_HOST in allowed or is_host_unchecked(path):
        return None
    raw = headers.get("host") or ""
    name = host_name(raw)
    if name is not None and (name in allowed or is_unbindable_host(name)):
        return None
    return f"Host {raw.strip()[:128]!r} is not a name of this Copycast"


def request_origin(scope: Scope, headers: Headers) -> str | None:
    """The origin the request was addressed to: its scheme (as uvicorn resolved it from a
    trusted proxy) and its ``Host``."""
    host = headers.get("host")
    if not host:
        return None
    return normalized_origin(f"{scope.get('scheme', 'http')}://{host}")


def cross_site_refusal(
    method: str, headers: Headers, scope: Scope, trusted: frozenset[str]
) -> str | None:
    """Why this request is refused as cross-site, or ``None`` when it may proceed.

    Fetch Metadata first: every current browser sends ``Sec-Fetch-Site``. Without it,
    ``Origin`` (``null`` included) must be ``base_url``'s origin or the request's own.
    With neither the client is no browser (curl, a podcast app, an MCP client): allowed.
    "Same origin" is only as good as the ``Host`` it is relative to: the caller checks
    :func:`foreign_host_refusal` first.
    """
    if method.upper() in SAFE_METHODS:
        return None
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        if fetch_site.strip().lower() in SAME_ORIGIN_FETCH_SITES:
            return None
        return f"Sec-Fetch-Site is {fetch_site.strip()[:32]!r}"
    origin = headers.get("origin")
    if origin is None:
        return None
    normalized = normalized_origin(origin)
    if normalized is not None and (
        normalized in trusted or normalized == request_origin(scope, headers)
    ):
        return None
    return f"Origin {origin.strip()[:128]!r} is not this Copycast"


CROSS_SITE_DETAIL = "another site cannot send this request; use the Copycast web UI or a client"
UNKNOWN_HOST_DETAIL = (
    "this Copycast is not served under this host name; use base_url, or add the name to "
    "COPYCAST_ALLOWED_HOSTS"
)


def refusal_response(path: str, *, slug: str, detail: str) -> JSONResponse:
    problem = build_problem(None, status=403, slug=slug, detail=detail)
    body = problem.model_dump(mode="json", exclude_none=True)
    body["instance"] = redact_text(path)
    return JSONResponse(
        body,
        status_code=403,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


class CrossSiteGuardMiddleware:
    """Refuse a request to a host name that is not Copycast's, and an unsafe method
    a browser sent from another site (403 problem+json)."""

    def __init__(self, app: ASGIApp, *, base_url: str, allowed_hosts: Iterable[str] = ()) -> None:
        self.app = app
        base = base_origin(base_url)
        self.trusted: frozenset[str] = frozenset({base} if base else ())
        base_host = host_name(urlsplit(base_url).netloc.rpartition("@")[2])
        names = {name.lower().rstrip(".") for name in allowed_hosts}
        self.allowed_hosts: frozenset[str] = frozenset(
            names | ({base_host} if base_host else set())
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method", "GET"))
        headers = Headers(scope=scope)
        path = str(scope.get("path", ""))
        slug, detail = UNKNOWN_HOST_SLUG, UNKNOWN_HOST_DETAIL
        reason = foreign_host_refusal(headers, self.allowed_hosts, path)
        if reason is None:
            slug, detail = CROSS_SITE_SLUG, CROSS_SITE_DETAIL
            reason = cross_site_refusal(method, headers, scope, self.trusted)
        if reason is None:
            await self.app(scope, receive, send)
            return
        log.warning("http.cross_site_refused", method=method, path=redact_text(path), reason=reason)
        await refusal_response(path, slug=slug, detail=detail)(scope, receive, send)


SPA_SCRIPT_HASHES: tuple[str, ...] = ("sha256-XZ7u7Kv4d6fvgp7L9MVTGFXnly56yiM72Fsf3EqvlFw=",)
"""The inline no-flash theme script of ``web/index.html``; a unit test recomputes it, so an
edit of that script fails the suite until this hash follows."""
DOCS_PATHS = frozenset({"/api/docs", "/api/docs/oauth2-redirect"})
DOCS_CDN = "https://cdn.jsdelivr.net"
"""FastAPI's Swagger UI loads its bundle and stylesheet from here and boots inline."""
PODCAST_PREFIXES = ("/feeds/", "/art/")
"""Feeds, media and assets: podcast apps render none of it, so no page policy (``nosniff``,
``Referrer-Policy`` and, but on audio and video, :data:`PODCAST_POLICY`)."""
PODCAST_POLICY = "sandbox"
"""A mirrored HTML transcript or SVG, or a feed's XML, opened in a browser is a document of
an opaque origin with scripts, forms and popups off. Audio and video are left alone (the
browser's own player document), and so are PDF Attachments: a sandboxed document may load no
plugin, and Chromium's PDF viewer is one, so it would show a blank page. The viewer runs a
PDF's own scripts, if at all, in its own sandbox, never on Copycast's origin."""
UNSANDBOXED_TYPES = ("audio/", "video/", "application/pdf")
HSTS_VALUE = "max-age=31536000"


def _policy(directives: dict[str, tuple[str, ...]]) -> str:
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())


def spa_policy(base_origin: str | None) -> str:
    """The CSP of the web UI and the API: same-origin scripts (plus the hashed inline theme
    script), styles that may be inline (Radix and sonner inject ``<style>`` elements),
    images from anywhere (a Catalog item that is not archived shows its Source's Artwork,
    and older feeds still serve it over plain http), media and fetches from this origin or
    ``base_url``'s (whose absolute ``media.url`` the player plays even when the UI is
    reached by another name). No eval: the web app turns zod's JIT off."""
    own = ("'self'", base_origin) if base_origin else ("'self'",)
    return _policy(
        {
            "default-src": ("'self'",),
            "script-src": ("'self'", *(f"'{digest}'" for digest in SPA_SCRIPT_HASHES)),
            "style-src": ("'self'", "'unsafe-inline'"),
            "img-src": (*own, "data:", "https:", "http:"),
            "media-src": own,
            "connect-src": own,
            "font-src": ("'self'",),
            "frame-ancestors": ("'none'",),
            "base-uri": ("'none'",),
            "object-src": ("'none'",),
            "form-action": ("'self'",),
        }
    )


def docs_policy() -> str:
    """The CSP of ``/api/docs``: Swagger UI from its CDN with its inline boot script; the
    page carries no data of its own."""
    return _policy(
        {
            "default-src": ("'self'",),
            "script-src": ("'self'", DOCS_CDN, "'unsafe-inline'"),
            "style-src": ("'self'", DOCS_CDN, "'unsafe-inline'"),
            "img-src": ("'self'", "data:", "https:"),
            "connect-src": ("'self'",),
            "frame-ancestors": ("'none'",),
            "base-uri": ("'none'",),
            "object-src": ("'none'",),
            "form-action": ("'self'",),
        }
    )


def is_podcast_path(path: str) -> bool:
    return path.startswith(PODCAST_PREFIXES) or path in ("/feeds", "/art")


class SecurityHeadersMiddleware:
    """Set the security headers a response lacks (see the module docstring)."""

    def __init__(self, app: ASGIApp, *, base_url: str) -> None:
        self.app = app
        base = base_origin(base_url)
        common = {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}
        browser = {
            **common,
            "x-frame-options": "DENY",
            "cross-origin-opener-policy": "same-origin",
        }
        if base is not None and base.startswith("https://"):
            browser["strict-transport-security"] = HSTS_VALUE
        self.podcast: dict[str, str] = common
        self.spa: dict[str, str] = {**browser, "content-security-policy": spa_policy(base)}
        self.docs: dict[str, str] = {**browser, "content-security-policy": docs_policy()}

    def headers_for(self, path: str) -> dict[str, str]:
        if is_podcast_path(path):
            return self.podcast
        if path in DOCS_PATHS:
            return self.docs
        return self.spa

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        wanted = self.headers_for(str(scope.get("path", "")))
        podcast = wanted is self.podcast

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in wanted.items():
                    if name not in headers:
                        headers[name] = value
                content_type = headers.get("content-type", "").lower()
                if (
                    podcast
                    and "content-security-policy" not in headers
                    and not content_type.startswith(UNSANDBOXED_TYPES)
                ):
                    headers["content-security-policy"] = PODCAST_POLICY
            await send(message)

        await self.app(scope, receive, send_secured)


__all__ = [
    "ANY_HOST",
    "CROSS_SITE_SLUG",
    "DOCS_PATHS",
    "HSTS_VALUE",
    "PODCAST_POLICY",
    "PODCAST_PREFIXES",
    "SAFE_METHODS",
    "SPA_SCRIPT_HASHES",
    "UNKNOWN_HOST_SLUG",
    "CrossSiteGuardMiddleware",
    "SecurityHeadersMiddleware",
    "base_origin",
    "cross_site_refusal",
    "docs_policy",
    "foreign_host_refusal",
    "host_name",
    "is_podcast_path",
    "is_unbindable_host",
    "normalized_origin",
    "request_origin",
    "spa_policy",
]
