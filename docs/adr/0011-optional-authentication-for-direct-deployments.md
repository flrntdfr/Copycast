# 0011 Optional authentication for direct deployments

Status: accepted (v1); narrows ADR 0004

## Context

ADR 0004 made the network the only security boundary: Copycast has no accounts and is
deployed inside a tailnet. People also run it on a plain host or behind their own reverse
proxy (the `direct` compose profile), where the network is not a boundary at all. Three
kinds of client reach such a deployment and they cannot share one mechanism:

- Browsers and scripts, which handle HTTP Basic natively and keep the pair for the session.
- Podcast apps, which fetch feeds and media unattended. Overcast, Pocket Casts and AntennaPod
  take a username and password (as `https://user:pass@host/…` or separate fields) and send
  it for episode downloads too; Apple Podcasts fetches the feed but not the audio.
- MCP clients. Claude Code sends a static header; Claude Desktop and claude.ai custom
  connectors accept OAuth only, which no static credential can satisfy.

Feed ids are hashes of the Source URL, so a feed URL is guessable and cannot be the secret.

## Decision

One switch, `COPYCAST__AUTH__PASSWORD`, turns authentication on. Off (the default) means
ADR 0004 unchanged. On, three credentials gate three surfaces:

1. **The operator password** (`auth.username`, default `copycast`, and `auth.password`) is
   required as HTTP Basic on the web UI, the API and the event stream. It also opens every
   feed route, so the operator can open a feed in the browser.
2. **Feed credentials**: every Feed carries its own random username and password, minted at
   creation, stored in clear in Postgres and in `feed.json`, and rotated on demand
   (`rotate_feed_credentials`). The three public routes of a Feed accept that Feed's pair or
   the operator's and nothing else, so a leaked pair opens one feed. While authentication is
   on, `feed_url` in every API and MCP response carries the pair as `user:pass@` and
   `feed_credentials` exposes it separately; enclosure and asset URLs inside the RSS stay
   bare because clients reuse the feed's pair for same-host downloads.
3. **API keys** gate the MCP mount, which takes keys only (`Authorization: Bearer cck_…`).
   Keys are minted and revoked from the UI, shown once, stored as a SHA-256 digest (the
   secret is random, so no salt or work factor is needed), and carry a scope: `read` for the
   read-only tools, `write` for everything but deletions, `full` for everything. Every tool
   checks the calling key's scope before it runs. The key capabilities and the rotation have
   routes but no tools: a key must never mint keys and an agent must never break every
   subscriber of a feed.

The health routes stay open for probes. Every 401 is problem+json with a `Basic` challenge
so browsers and podcast apps prompt for the pair; the MCP mount answers with a `Bearer`
challenge.

## Consequences

- Basic auth is only meaningful over TLS: a direct deployment on the internet needs a TLS
  terminator in front, which the compose `direct` profile does not provide.
- Switching authentication on, or rotating a feed, breaks that feed's existing subscriptions
  until the client is given the pair. The UI shows it next to the feed URL for that reason.
- Apple Podcasts cannot download audio from an authenticated feed; other clients can.
- `copycast rebuild` restores each feed's pair from `feed.json`; a descriptor written before
  this ADR gets a fresh pair. Migration 0002 mints one for every existing feed.
- Claude Desktop still needs OAuth; keys are not a step toward it. If that ever matters it is
  a separate provider on the same mount, not a change to this design.
- The plain-text feed pairs and the operator password appear in `feed_url`; API responses are
  already `Cache-Control: no-store`.
