# 0025 The browser boundary: cross-site and foreign-host requests, security headers, trusted proxies, no secret in a log

Status: accepted (2.3.0); narrows ADR 0004 (Copycast now refuses cross-site writes and
checks the Host of every request but `/healthz`, with authentication on or off) and amends
ADR 0013 (what the request log calls the client's address)

## Context

ADR 0004 left Copycast with "no CORS policy, no trusted-host check and no CSRF protection",
on the ground that the network is the boundary. A browser carries the network boundary with
it. Any page the operator visits can `POST` to a tailnet Copycast from inside the tailnet,
and a browser attaches a cached Basic pair to such a request when authentication is on. A
page whose name the attacker rebinds to Copycast's address is same-origin with itself and
can read the answers too (DNS rebinding). Mirrored HTML transcripts and SVG Attachments are
served from Copycast's own origin, where a script in them would run with the operator's
pair. Copycast also sent no security headers. Two more findings concerned logs. The request
log believed `X-Forwarded-For` from anyone, so a client could pick its own `client_ip`.
Credentials could reach log lines in URLs, headers or tracebacks.

## Decision

**Cross-site guard** (`adapters.api.security.CrossSiteGuardMiddleware`). It runs ahead of
authentication and of every route (`/api`, `/uploads`, `/mcp`, `/feeds` alike), whether
authentication is on or off. Each refusal logs `http.cross_site_refused` with the method, the
redacted path and the reason.
- *Host.* Every request but those to `/healthz` (probes), whatever its method and headers,
  must name Copycast in `Host`. That is `base_url`'s host, a name listed in
  `COPYCAST_ALLOWED_HOSTS` (comma-separated, `*` turns the check off), an IP literal, or
  `localhost` and its subdomains: no attacker can rebind any of those. Any other `Host` gets
  `403 unknown-host`. The check first applied only to requests carrying `Sec-Fetch-Site` or
  `Origin`, but rebinding matters only over plain http (over https the certificate gives the
  rebound name away), and there a browser sends no Fetch Metadata (it sends it to
  trustworthy origins only) and a same-origin `GET` no `Origin`: the rebound page read the
  whole API with neither header.
- *Unsafe methods.* Anything but `GET`, `HEAD` and `OPTIONS` is refused with
  `403 cross-site-request` when `Sec-Fetch-Site` is present and is neither `same-origin` nor
  `none`. Without Fetch Metadata the request is refused when `Origin` (`null` included) is
  neither `base_url`'s origin nor the request's own.
- Clients that send neither header (curl, podcast apps, desktop and CLI MCP clients) are
  never refused as cross-site. They pass the host check by using a name of this Copycast,
  as podcast apps do with the feed URLs Copycast publishes.

**Security headers** (`SecurityHeadersMiddleware`, outside the guard and authentication, so
their 403 and 401 carry them too; a header a route set itself wins):
- every response: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`;
- everything but `/feeds/` and `/art/`: a Content-Security-Policy, `X-Frame-Options: DENY`,
  `Cross-Origin-Opener-Policy: same-origin`, and `Strict-Transport-Security: max-age=31536000`
  when `base_url` is https. The web UI's policy allows same-origin scripts plus the hashed
  inline theme script (`SPA_SCRIPT_HASHES`), inline styles (Radix and sonner inject `<style>`
  elements), images from anywhere over https and http (a Catalog item not yet archived shows
  its Source's Artwork), and media and fetches from this origin or `base_url`'s. It sets no
  eval, no frames, no plugins, `base-uri 'none'` and `form-action 'self'`. The web app turns
  zod's JIT off so that it never needs eval. `/api/docs` gets a looser policy for Swagger UI's
  CDN bundle and inline boot script;
- `/feeds/` and `/art/`, everything but audio, video and PDF: `Content-Security-Policy: sandbox`.
  Podcast apps ignore it. A browser opening a mirrored HTML transcript, an SVG or a feed's XML
  gets an opaque origin with scripts, forms and popups off. The plan asked for `nosniff` and
  `Referrer-Policy` only on these paths. The sandbox goes further, and costs nothing a podcast
  app uses. PDF Attachments are left out: a sandboxed document may load no plugin, and
  Chromium's PDF viewer is one, so they would open blank; the viewer never runs a PDF's
  scripts on Copycast's origin.
- The SPA's catch-all never answers under `/art` or `/uploads` (nor `/api`, `/feeds`, `/mcp`,
  `/healthz`, `/assets`), so the shell never appears where its headers are missing.

**Trusted forwarded headers.** `copycast api` runs uvicorn with `proxy_headers` and
`forwarded_allow_ips` from `COPYCAST_FORWARDED_ALLOW_IPS`: comma-separated addresses, CIDR
networks or `*`. It is environment-only, ignored (silently) in the TOML file because it
belongs next to the proxy it names. Unset keeps uvicorn's default: its own `FORWARDED_ALLOW_IPS`, else
`127.0.0.1`, which already covers the Tailscale sidecar proxying from loopback. A bad entry
stops the process with a one-line error. The request log's `client_ip` is the address uvicorn
resolved: the socket peer, or the `X-Forwarded-For` hop a trusted proxy named.
`forwarded_for` stays the raw header, which a client can forge. The request's scheme comes
from the same proxies.

**No secret in a log line** (`copycast.logging`). Every line, from structlog or stdlib, JSON
or console, tracebacks included, masks URL userinfo (`scheme://***@host`, both halves), media
keys (`/k/***/`), upload tokens (`/uploads/{id}/***`), API keys past their display head
(`cck_XXXXXXXX***`), `Authorization` and `Cookie` values, and Basic, Bearer and Digest
credentials. The console renderer prints plain tracebacks, without frame locals or line
wrapping. A JSON line is never re-redacted after rendering, so it stays valid JSON.

## Consequences

- A browser-based MCP client on another origin (MCP Inspector in a browser tab, say) is
  refused on `/mcp` (it sends `Sec-Fetch-Site: cross-site`). Desktop and CLI clients are
  not. FastMCP's own host and origin protection stays off: the guard covers `/mcp`.
- Reaching the UI or the api by a name other than `base_url`'s (a MagicDNS short name, a
  LAN alias, a compose or Kubernetes service name an in-cluster client uses) needs that name
  in `COPYCAST_ALLOWED_HOSTS`. Probes are unaffected: `/healthz` is not checked.
- Behind Traefik or another proxy on a Docker or pod network, `COPYCAST_FORWARDED_ALLOW_IPS`
  must name that network, or `client_ip` is the proxy's address and the scheme stays `http`.
  The Kubernetes base carries the variable commented out, with the pod CIDR to set.
- Editing the inline theme script in `web/index.html` means updating `SPA_SCRIPT_HASHES`.
  A unit test fails until it is. Playwright specs import `test` from `web/e2e/fixtures.ts`,
  which fails any test that meets a CSP violation.
- `style-src 'unsafe-inline'` stays, since removing it would need a per-response nonce the
  static SPA shell cannot carry. `/api/docs` loads an unpinned Swagger UI from a CDN behind
  the operator pair. Self-hosting it would allow the strict policy there too.
- HSTS is not sent on `/feeds/` and `/art/`, but it applies host-wide, so the UI's header
  covers browsers anyway.
- A reverse proxy logs before Copycast does: Traefik's access log records media keys and
  upload tokens in `RequestPath` (docs/operations.md, "Upgrading to 2.3.0").
- The other flat `COPYCAST_*` knobs, `COPYCAST_ALLOWED_HOSTS` included, are still readable
  from the TOML file. Only `COPYCAST_FORWARDED_ALLOW_IPS` is dropped there, without an error.
