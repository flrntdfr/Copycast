# 0028 Feeds are private to their Owner; another User's Feed does not exist for you

Status: accepted (3.0.0); drops the shared Library of the multi-user plan (27 Oct); amends
ADR 0024 (per-Mirror `engine_options` are Admin-only) and ADR 0003 (every capability declares
its access)

## Context

With users from the config file (ADR 0027), each Feed needs an Owner and a rule for who sees
it. The 27 Oct plan proposed a shared Library: one copy per Source, subscribed to by many
Users. The 2026-11-05 decision D1 chose the simplest model instead: each User owns their
Feeds and nobody else's, two Users mirroring one Source keep two copies, and Admins see
everything. Feed ids were `sha256(dedup_key)[:16]`, which anyone can compute from a Source
URL, and a Watch Later Capture had the same id on every install, so ids alone could reveal
what someone mirrors. The probe cache, the Defaults, the Cookie files, the event stream and
the status counters were all instance-wide.

## Decision

**Every Feed has an Owner.** `feeds.owner` (migration 0017) names a User; `api_keys.owner`
and `jobs.requested_by` follow, and an Upload's owner is a key of its `upload.json`. A Feed
is created by the acting User: Admins create as themselves, never for someone else. Each
User has their own default Local feed "Copycast" (unique per owner, created for every
configured User at startup), and a Source's dedup key is unique per owner, not per instance.
`feed.json` carries the owner, and `copycast rebuild` restores it (a descriptor without one
goes to `owner_of_existing`).

**Visibility is built into the unit of work.** The services an HTTP request or an MCP call
gets are `services_for(principal)`, whose repositories filter every Feed lookup (and those of
items, Requests, jobs and telemetry, through their Feed) by `Scope.for_principal`:

| Principal | Sees |
|---|---|
| SYSTEM (the worker, the scheduler, startup, public `/feeds` routes) | every Owner |
| An Admin's session, the open-mode Admin | every Owner |
| A Member's session | their own |
| Any API key, an Admin's included | its owner's own |

A Feed you cannot see behaves exactly like a missing one: **404 `not-found`, never 403**, for
the Feed and for its items, Requests, jobs, objects, events and Uploads. A key acts as its
owner and never more: what it can do is its scope, intersected with its owner's role and its
owner's Feeds, so the Admin's MCP key on 3.dufour.xyz never sees another User's Feeds. No tool
takes a user id.

**Roles are declared per capability.** The registry gives every capability an `Access`:
`public` (its own credential: `login`, `put_upload`'s token), `member` (acts on the caller's
own things), `owner_of_feed` (reaches a Feed by id, 404 unless visible), `admin` and
`system`. The decorator refuses `admin` capabilities to a non-Admin with `403 forbidden` (a
role is no existence secret) and `system` ones to anything but SYSTEM. The convergence test
fails on a capability without an `Access`, and an isolation matrix generated from the
registry and OpenAPI runs every `owner_of_feed` route and MCP tool as one Member against
another Member's ids and expects 404, every `member` listing without a trace of the other's
ids, titles or Source URLs, and every `admin` capability to answer 403. An AST test bans the
raw unit-of-work factory, the SYSTEM services and `container.services` from `adapters/api`
and `adapters/mcp` outside a short allowlist.

| | Admin | Member |
|---|---|---|
| Own Feeds: create, policy, delete, Uploads, API keys | yes | yes |
| Other Users' Feeds | yes, labelled with the owner | 404 |
| Own Defaults and Cookie files | yes | yes |
| Captures with their own cookies | yes | yes |
| `rebuild`, `purge_episodes` (every User's unlocked Episodes) | yes | 403 |
| A Mirror's `engine_options` | yes | 403 when set or changed |

API keys are listed and revoked by their owner only, Admins included; minting one needs a
session.

**New Mirror ids are salted with the Owner in users mode.** Every new Mirror and Capture in
users mode gets `sha256(owner + "\n" + dedup_key)[:16]`, whether or not anyone else mirrors
the Source, so one User's id is the same whether or not another mirrors it and comparing it
with `sha256(dedup_key)[:16]` tells nothing. (The plan kept the unsalted id for the first
owner; that let a Member prove another User mirrors a Source. The product owner accepted the
change on 2026-11-06.) Open and legacy mode keep `sha256(dedup_key)[:16]`, byte for byte as
2.3.0. Existing ids never change: podcast apps depend on them (ADR 0011, ADR 0020). Creating
a Mirror reports `feed-exists` only for the actor's own duplicate; another Owner's Feed of the
same Source is invisible, and an id collision (a 64-bit hash clash) is a bare `409`.

**Settings follow the Owner.** Defaults live at `users/{username}/defaults.json`, Cookie files
at `users/{username}/cookie-files/` with the merged jar `users/{username}/cookies.txt`. Every
Engine call made for a Feed (Refresh, archive, Request expansion, metadata, Light Refresh)
carries its Owner's jar, also when an Admin acts on it; a probe, a search or the playlists
listing carries the acting User's. The probe cache and the podcast search cache are keyed by
Owner, so a listing made with one User's cookies is never served to another. Each User sees
and changes only their own Defaults and Cookie files; cookie contents are never returned to
anyone, Admins included. The scheduler applies each Owner's own Refresh interval. 3.0
**copies** the global `engine/` keys of 2.x to `owner_of_existing` once (a marker key makes it
run once) and never deletes them, so a 2.3.0 image rolled back still finds them; a later
release deletes them.

**Events, status and storage are per Owner.** The NOTIFY payload carries the event's Owner;
a stream delivers an event to its Owner, to Admin sessions and to SYSTEM, and a resync to
everyone. Event ids are numbered per Owner view, so a Member can neither count nor time other
Users' events. `get_status`, `about` and `storage_summary` count the caller's own Feeds
(`storage_summary` stays the caller's own for Admins too, as the plan says); the instance-wide
facts of the Object store and every User's quota need a Principal that sees every Owner, an
Admin session or SYSTEM, never a key.

**Removed users keep their data.** A user taken out of the file keeps their rows: their
sessions are deleted at startup, their keys and feed pairs answer the uniform 401, their Feeds
are not scheduled for Refresh, autoprune or quota release, and Admins see them with
`owner_removed`. Adding the user back restores everything. Feeds change owner only through
`copycast users reassign OLD NEW` (run with the worker stopped): it moves Feeds, jobs and
`users/OLD/` to NEW in one transaction, re-exports every moved `feed.json`, refuses when OLD
and NEW mirror the same Source, turns OLD's default Local feed into an ordinary one, and moves
API keys only with `--move-keys`, since a moved key acts as NEW with NEW's powers. Feed ids,
pairs and media keys do not change.

## Consequences

- Two Users mirroring one Source download and store it twice; quotas (ADR 0029) count each
  copy for its Owner. A shared Library would be a separate decision.
- A Feed id no longer tells whether a server mirrors a Source in users mode; pre-3.0 ids of
  `owner_of_existing` are still the unsalted hash, as before.
- `/feeds/{id}.xml` also opens with the Owner's or an Admin's session, so a Member's "feed XML"
  link works in the browser; media and asset paths still need the pair or the media key, and
  the web player uses `/api/feeds/{id}/items/{item}/media` with the session instead (U10).
- Moving Feeds between Users has no UI; Admins manage nobody else's Defaults, Cookie files or
  API keys.
- Restoring a Feed from `feed.json` keeps its owner, even one no longer configured, so a
  rebuild never orphans a removed user's Feeds onto someone else.
