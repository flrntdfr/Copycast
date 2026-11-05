# 0023 The fetch guard: no Source-driven fetch reaches an internal address

Status: accepted (2.3.0); amends ADR 0002 (the Engine's own connections pass the guard) and
ADR 0004 (being inside the network no longer makes Copycast a way into it)

## Context

Whoever adds a Source makes the server fetch URLs of their choosing: any write credential,
and with authentication off anyone on the network. Inside a deployment those URLs can name
the Object store (`http://seaweedfs:8333`, which holds every Feed's files), Valkey, Postgres,
the Tailscale sidecar or a cloud metadata service (`169.254.169.254`), and error texts told
the caller what answered (SEC-4, finding U5 of the configuration and users plan). The plan
asked for one guard in front of the probe, directory lookups, the Atom feed, Attachments and
RSS Sources, plus the same check on the URL handed to yt-dlp. Review found that yt-dlp's own
later requests (redirects, manifests, formats, thumbnails) were a second way in, so the
Engine's connections are guarded too.

## Decision

**What is refused** (`domain.destinations`): every address that is not plainly on the public
internet. That is loopback, private (RFC 1918), shared (CGNAT `100.64/10`), link-local
(`169.254/16`, `fe80::/10`), unspecified (`0/8`, `::`), multicast, reserved and broadcast,
unique-local `fc00::/7`, site-local `fec0::/10`, 6to4, NAT64 local-use `64:ff9b:1::/48`, and
whatever else `ipaddress` does not call globally reachable. An IPv6 address that carries an
IPv4 one (IPv4-mapped, IPv4-compatible, NAT64) is judged by the address it carries. Schemes
other than `http` and `https` are refused.

**Copycast's httpx client** (`adapters.sources.guard`). Every client of
`adapters.sources.http` uses a `GuardedTransport`. Its backend resolves the host once, judges
every answer, and connects to a validated address literal. The name is never resolved a second
time, so a DNS answer that changes between the check and the connection (rebinding) cannot
slip an internal address in. TLS still verifies, and SNI and `Host` still name, the URL's host.
Redirects are followed one hop at a time and every new origin is a new, checked connection.
One refused answer refuses the whole host; Copycast does not fall back to the name's public
addresses. This covers the probe and the feeds a page or directory advertises, iTunes search
and directory lookups, RSS Refreshes and their next pages, Light Refreshes, the YouTube Atom
feed, Request expansion, Artwork, chapters, transcripts and Attachments.

**The Engine.** Copycast checks the URLs it hands yt-dlp (`check_engine_url`): the listing's
Source URL, the item URL and a direct fetch's enclosure. Search keys (`ytsearch10:…`) pass. An
Engine key with no `//authority` and no local scheme (`nrk:…`, `:ytwatchlater`) passes when one
of yt-dlp's own extractors, the generic one aside, claims it. `file:` and `data:` never pass,
and a refused embedded-Artwork URL is dropped instead of failing the archive. Then
`adapters.engine.netguard`, installed by the Engine, puts the same check on every TCP
connection yt-dlp opens. It rebinds `create_connection` in yt-dlp's urllib and websockets
handlers (SOCKS proxies included) and gives the requests handler's urllib3 pool managers
guarded pools. Each connection resolves once and connects to the checked literal. A configured
proxy is judged as the peer. Any other request handler (curl_cffi impersonation) is
unregistered, and a unit test fails when a yt-dlp bump adds a handler module.

**Exceptions** are the operator's: `[security] allow_private_hosts`
(`COPYCAST__SECURITY__ALLOW_PRIVATE_HOSTS`, a JSON list), empty by default. An entry is a
hostname, matched exactly (case-insensitive, trailing dot ignored, IDNA), which may then
resolve to any address, or an IP network in CIDR form (a bare address is one host), which
exempts the addresses inside it whatever name led there. A bad entry stops the process at
startup with a one-line error. The policy is process-wide: building the container installs it.

**Refusals** are `DestinationNotAllowed`, answered as `422 destination-not-allowed` ("Destination
not allowed") by the API and as `[destination-not-allowed]` by MCP tools. The message names
the host and port and gives one generic reason ("it resolves to a non-public address (an
operator can exempt it with security.allow_private_hosts)"). The address and its class go to
the `fetch.destination_refused` log event only. Probe, create and retarget refuse at once. In
the worker a refused Refresh, archive or Request expansion fails once as a permanent error
(never retried) and the Mirror keeps serving what it has. A refused Attachment or Artwork
marks only that asset failed. A feed link that a page or directory advertises and the guard
refuses is skipped like a broken one, so the probe offers the public ones.

**Not guarded:** the Object store client and the Feed cache client, which are
infrastructure and talk to configured endpoints.

## Consequences

- Sources on the operator's own network (a NAS, a self-hosted podcast server, a compose
  service name) must be listed in `allow_private_hosts`, or probes answer 422 and Refreshes
  fail. `make dev` against a local test server needs `localhost` listed. The e2e compose
  file exempts its `fixtures` service by name.
- Server-side httpx fetches no longer use `HTTP_PROXY` / `HTTPS_PROXY` from the
  environment: httpx ignores them once a transport is given, and a proxy would connect on
  the guard's behalf anyway. yt-dlp still honours `proxy` from `[engine.options]`. A proxy on
  a private address must be listed in `allow_private_hosts`.
- Outside the guard: processes yt-dlp starts itself (ffmpeg as the downloader of a stream it
  cannot fetch natively, rtmpdump) connect on their own. A refusal in a concurrent fragment
  thread is still blocked, but may be reported as a transient error rather than as
  `DestinationNotAllowed`.
- A Request (`add_to_bucket`, `POST` of a Request) with an internal URL is accepted by the
  API and fails in the worker's expand job with the refusal as its reason.
- An unresolvable name and a refused name still give different probe answers
  (`source-unsupported` and `destination-not-allowed`). The only thing left to learn from
  that is whether a name resolves to some non-public address.
- `GuardedTransport` replaces httpx's private `_pool`. httpx is pinned, and tests assert
  that loopback is refused and that the client has no proxy mounts, so a bump that breaks
  this fails CI instead of dropping the guard silently.
- Tests never use real DNS: an autouse fixture resolves address literals and `localhost`
  and nothing else (`tests/support/dns.py`), and the suite exempts loopback for its local
  fake servers (`TEST_ALLOW_PRIVATE_HOSTS`). Tests marked `network` keep the real resolver.
