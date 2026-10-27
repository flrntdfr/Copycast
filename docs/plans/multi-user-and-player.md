# Plan: multi-user, identity, secure feed URLs, and a better web player

Status: **draft for decision**, 2026-10-27. Based on `main` at `f1436b0` (1.3.1). Nothing has been built yet.

How this plan was made:
- Five parallel readers covered the auth code, the player code, a UI/UX audit, identity-provider research and comparable apps.
- Three architects each designed the auth stack from a different stance: in-app, delegate to Authelia, or hybrid. Three judges scored the designs through a security lens, an operations lens and a codebase lens. A synthesis merged the result.
- Player ideas came from four brainstorming lenses (56 ideas), followed by prioritisation and a completeness critic.
- The load-bearing code claims below were re-checked by hand.

Production facts it relies on (3.dufour.xyz, read-only check):
- Traefik v3.7.13 on the docker provider. The file provider's dynamic config is empty.
- Nextcloud 32 runs at nuage.dufour.xyz with no OIDC app installed. A tailscaled container is also present.
- **No Authelia or other IdP is deployed.**
- Copycast 1.3.1 runs with the Operator password on, 23 Feeds and 2 API keys.
- Traefik writes a JSON access log that **records request paths**.

---

## 1. The short answer

| Question | Recommendation |
|---|---|
| Who owns identity? | **Decided: Copycast, in-app.** Accounts are created by invite, passwords are argon2id, a server-side session sits behind a host-only `__Host-` cookie, and **passkeys are mandatory for Admins** (on https). No new service on 3.dufour.xyz. A `Principal` seam keeps an OIDC adapter possible later (Pocket ID, not Nextcloud; see §2.3). |
| Authelia? | **Not now.** It would protect only the UI and `/api`, and those are the cheap part. It changes nothing for feeds, media or MCP, which must bypass any proxy. It also adds an internet-facing container, header-trust risk (any container on the `web` network can forge `Remote-User`), a parent-domain cookie that reaches Nextcloud, and offboarding in two places. |
| What happens to secure URLs? | The secret moves from **per Feed** to **per (User, Feed)**: the **Feed key**. It comes in two forms: the **key pair** (`key_id:secret@host/feeds/{id}.xml`, bare enclosures, exactly today's shape) and the **key link** (`/feeds/k/{key_id}.{secret}/{id}.xml`, with the token repeated in every enclosure, so Apple Podcasts works). **The 23 existing Overcast URLs stay byte-identical.** |
| Shared or separate archives? | **Decided: full Library.** One shared archive per Source. Each User subscribes; the creator is the Owner and governs policy. **Private Mirrors** are used for Captures and for Sources that carry credentials. |
| Player first? | **Yes.** It needs no accounts: a 0.5–3× speed slider, resume, Media Session, a sleep timer and the chapter fix ship in 1.4. Listening positions move to the server in 1.5, keyed by `users.id` from day one. |
| Effort | Player 1.4: ~8–10 person-days. Server positions plus the web media route: ~10–13. Feed keys: ~6–8. Accounts and Library (2.0): ~19–26. Estimates, not measurements. |

---

## 2. Identity: build in app vs Authelia vs the alternatives

### 2.1 Why an IdP cannot solve the hard part

Three kinds of client reach Copycast, and only one of them can do SSO:

| Client | Can do cookies, OIDC or 2FA? | What it needs |
|---|---|---|
| Browser (UI, `/api`, SSE) | Yes | Any login: in-app or IdP |
| Podcast apps (Overcast crawls server-side from `crawl1-6.overcast.fm`; media is downloaded on the phone) | **No** | A static secret in the URL: Basic `user:pass@`, or a token in the path |
| MCP (Claude Code: static Bearer; claude.ai and Desktop: OAuth only) | Claude Code no; claude.ai OAuth only | API key, or OAuth with CIMD/DCR |

So `/feeds/*`, `/mcp` and `/.well-known/*` **must bypass any forward-auth proxy and keep Copycast's own secrets**. Every comparable app does the same: Audiobookshelf, PinePods, PodFetch, Navidrome, Patreon/Supercast. Authelia's `HeaderAuthorization` (Basic through Authelia) would put a real single-factor Authelia password onto Overcast's servers and collide with the feed pair in the same header. An IdP therefore covers the browser surface only, and that is the surface in-app sessions cover in about 9–12 person-days.

### 2.2 Comparison

| | **In-app accounts** (recommended) | **Authelia forward-auth** | **Copycast as OIDC client** (Nextcloud `oidc` app or Pocket ID) | **Tailnet-only admin plane** | **Hybrid (all of the above, pluggable)** |
|---|---|---|---|---|---|
| Browser security | argon2id, `__Host-` cookie, Fetch-Metadata CSRF, Admin passkeys; nothing trusts headers | 2FA and passkeys from day one; Copycast must trust `Remote-*` headers (needs a shared secret, a dedicated network, fail-closed checks) | No passwords in Copycast; the IdP's 2FA; no header trust | Tailscale identity; only `/feeds/` public | Largest test matrix; weakest defaults |
| Feeds and media | Feed keys (same in every column) | Same, plus a bypass router; a priority mistake shows Overcast a login page | Same | Same; only `PathPrefix(/feeds/)` public via Traefik | Same |
| New moving parts on 3.dufour.xyz | None (one HSTS label) | Authelia container, users YAML, 4 secrets, SQLite, Redis or in-memory sessions (a restart logs everyone out), file-provider middlewares | Nextcloud app (one maintainer; its DCR clients expire after 1 h) or a Pocket ID container | `tailscale serve` config; every family member joins the tailnet | Everything |
| Copycast effort for login | 9–12 pd (sessions, passkeys, invites, CLI recovery) | ~6 pd (header adapter + hardening) + ops | 3–4 pd (OIDC client) + local fallback for the bootstrap Admin | 2–3 pd, but TS identity headers are **unproven** under `TS_USERSPACE=true` | 40+ pd |
| MCP OAuth for claude.ai | Hardest alone (Copycast as authorization server, 6–9 pd) | Static client works ("Use your own OAuth client"); DCR waits for Authelia 4.40 | **Best path**: fastmcp `OIDCProxy` (already pinned, 4.0.3) in front of the IdP offers CIMD+DCR to Claude, 5–6 pd | n/a | `OIDCProxy` |
| Other self-hosters | Multi-user works as soon as `COPYCAST__AUTH__PASSWORD` is set | Single-user forever without an IdP | Needs an IdP | Needs Tailscale | Most options |
| Verdict | **Chosen for 2.0** | Runner-up; revisit only if you want one login across many homelab apps | **The planned add-on** (M5) when claude.ai OAuth matters | Rejected for family use | Rejected |

Two of three judges chose in-app; the codebase-lens judge preferred Authelia, mainly because it reuses battle-tested login code.

**ADR 0004 is not reversed.** With auth off (tailnet deployments), one implicit Admin behaves exactly as today. Accounts extend ADR 0011, which already moved credentials into the app for direct deployments.

### 2.3 Deep-dive: in-app vs Nextcloud vs a dedicated IdP (asked 2026-10-27)

> **Decision (2026-10-27): in-app accounts.** The deep-dive below is kept as the record. If a second multi-user service arrives later, add the OIDC client with Pocket ID as described here (about 6.5–10 pd). `users.external_subject` stays reserved for that. The ops prerequisites that apply only to an IdP (the NAT fix, the `id` router) are not needed now. **The Traefik access-log action is still required before key links ship.**

You asked this because other people may get access to other services as well. Six agents researched it: Nextcloud as an IdP, the dedicated IdPs, and a Copycast-side OIDC design. Two adversarial fact-checkers then reviewed the result, and both said the recommendation holds. Their corrections are folded in below.

**Answer.** If other people will use other services, put their login in **one identity provider, and do not use Nextcloud for it**.
- **Recommended:** Copycast becomes a generic **OIDC client** for any issuer, with **one break-glass local Admin**, and **Pocket ID** runs at `id.dufour.xyz`.
- **Runner-up:** in-app accounts.
- The two paths are about equally reversible: adding the other one later costs roughly 5–10 pd either way. So the deciding question is whether **a second multi-user service is likely within a year**. If yes, Pocket ID. If no, in-app accounts are 2–3 pd cheaper and add no service to run.

**Why not Nextcloud** (checked today on the server and in the app's sources):
- **No accounts to reuse.** Nextcloud has **1 user (you) and no second factor**.
- **End of life.** Nextcloud 32 is EOL (32.0.15 was the last release), so 32→33→34/35 upgrades are due anyway, and each maintenance window would block every SSO login.
- **A fragile IdP app.** The `oidc` IdP app (H2CK, 2.6.1) has one maintainer and is mid-rewrite. It shipped 26 releases since June, and at least four broke sign-in for some setups. A high-severity authorization-response hijack was fixed only in 2.5.0 (October).
- **Weak login guarantees.** Passkeys cannot be made mandatory, because the password stays valid. The app emits no `amr`, so Copycast cannot check for a second factor. There were four 2FA-bypass CVEs in 2024–26.
- **Every invitee gets an account in your private file cloud.** There are no invite links, activation needs SMTP, and people see two brands plus a consent screen.
- **Shared lockout.** Nextcloud's brute-force throttle works per IP, and every client is 172.19.0.1, so one bot locks everyone out.
- **Blast radius.** A breach yields tokens for every connected service **plus your files**.
- **claude.ai.** No CIMD; DCR clients are capped at 100 and expire after 1 h; `resource` needs admin approval per client; refresh tokens last 900 s by default. It works only behind fastmcp `OIDCProxy`.

| | In-app accounts | Nextcloud as IdP | **Pocket ID** (dedicated IdP) |
|---|---|---|---|
| Login security | Copycast builds and owns argon2id, passkeys (mandatory for Admins) and backoff | Password today; passkeys optional; no `amr` | **Passkey-only accounts**, configurable user verification, `amr` |
| Onboarding a non-technical person | Copycast invite link, then password or passkey | Create a Nextcloud account (no invite links), add a group, set quota 0 | **Signup link** with expiry, use limit and groups; create a passkey on the phone; done |
| Access to other services | None: every app keeps its own accounts | OIDC apps (Forgejo or Gitea on git.dufour.xyz…) | OIDC apps, per-app group restrictions; Nextcloud itself can log in through it (`user_oidc`) |
| Apps without OIDC | n/a | oauth2-proxy or tinyauth upstream | Same gateways (tinyauth, oauth2-proxy); the header-forgery caveat applies to both |
| claude.ai MCP OAuth | Copycast as its own authorization server (6–9 pd) | Only via `OIDCProxy` (+3.5–4.5 pd) | **CIMD native**: Copycast as resource server (+2–4 pd, needs a spike); `OIDCProxy` as fallback |
| Outage / blast radius | Copycast only | Every SSO login; breach = all services + files | New browser logins only (existing sessions survive); breach = connected services, not files |
| Ops on 3.dufour.xyz | Nothing new | No container, but the EOL upgrades, the app, 2FA enforcement and token tuning | One small Go container on SQLite, one DNS name, backups of data + `ENCRYPTION_KEY`, about **1–2 pd/year** of patching (16 advisories in 2026, 1 critical and 5 high, all fixed fast; one lead developer plus two co-maintainers) |
| Copycast effort (2.0 login) | 9–12.5 pd | 11–15.5 pd | 11–15.5 pd (+2–3 for an optional SCIM receiver) |
| Offboarding | One switch in Copycast, repeated in every other app | Disabling in Nextcloud stops logins; Copycast suspends sessions | Disabling once stops logins everywhere. **Feed keys and API keys still need a Copycast disable**, unless the SCIM receiver is built |
| Lock-out recovery | CLI `copycast users reset` | `occ` | `pocket-id one-time-access-token <user>`; Copycast's CLI break-glass link while the IdP is down |
| Other self-hosters | Multi-user with one environment variable | Any OIDC issuer; without one, single-user until local Members ship (+4.5–6.5 pd) | Same |

**Why Pocket ID and not another dedicated IdP.** It is the only candidate with invite links, passkey-only accounts, CIMD and SCIM push together.
- **Authelia + LLDAP** is the alternative if several apps without OIDC need a login wall. Its downsides: YAML administration, no invites, no CIMD, DCR or SCIM, and a passkey counts as one factor unless an experimental option is set.
- **Kanidm** forces an upgrade every ~3 months.
- **Authentik** needs ~2 GB of RAM.
- **Keycloak** is a 1.25 GB JVM.
- **Zitadel** ignores `resource` and had a run of account-takeover advisories in Aug–Oct 2026.

**What changes in this plan if D2 = OIDC client + Pocket ID:**

1. **Ops prerequisites (before Pocket ID).**
   - Diagnose and fix the 172.19.0.1 NAT. Check `daemon.json`, look for `docker-proxy` on :443/[::]:443, and compare IPv4 with IPv6, then fix in a maintenance window. Pocket ID rate-limits per IP too, so this is a hard prerequisite.
   - Turn off Traefik access logging on the `id` and `copycast` routers, or drop `RequestPath` and query parameters globally. Pocket ID's one-time links and Copycast's key links both live in paths.
2. **1.5.** Add `user_identities(user_id, issuer, subject, last_login_at)`, unique on `(issuer, subject)` and empty for now. **Never link accounts by email.**
3. **Before 2.0.**
   - Deploy Pocket ID and enrol two passkeys.
   - Spend half a day on a spike that checks: `sub` stability, `groups`, `amr`, back-channel logout, SCIM on disable vs delete, the CIMD discovery fields, and the hairpin from the Copycast api *and* worker containers to `id.dufour.xyz`. The worker is not on `web` today.
4. **2.0 login: 11–15.5 pd, so 2.0 totals 21–29.5 pd.**
   - **What stays:** the shared core (sessions, `__Host-` cookie, Users page, disable cascade).
   - **What is new:**
     - a generic OIDC client on authlib, already in the lock through fastmcp; add it as a pinned direct dependency. It does PKCE, nonce, `iss`/`aud`/`azp` checks and an algorithm allowlist.
     - ungated `/auth/oidc/*` and `/.well-known/oauth-protected-resource` routes.
     - a break-glass link minted by `copycast users break-glass`.
     - a fake OIDC provider in `tests/support` with negative tests.
     - the code goes into the pre-2.0 `/security-review`.
   - **What disappears:** local passkeys, invites and the backoff tables. The IdP does that work.
5. **The auth-on switch** becomes "an issuer is configured", or an explicit `auth.mode`. The Operator password is **retired** after the legacy release, not narrowed.
6. **Roles.** The IdP group `copycast` only gates entry; **Admin or Member is set in Copycast**, so a group edit in the IdP can never create a Copycast Admin.
7. **Offboarding.** Disabling in Copycast stays authoritative. IdP signals only *suspend*, which is reversible:
   - sessions are re-checked every 15 min, with a daily sweep;
   - an ambiguous `invalid_grant` never kills Feed keys.
8. **Later, optional:**
   - claude.ai directly against Pocket ID through CIMD (2–4 pd after a spike);
   - the SCIM receiver (2–3 pd);
   - local Member accounts for self-hosters without an IdP (4.5–6.5 pd);
   - Nextcloud as a Pocket ID client, only after its upgrade and only when a second person needs files.

**Deployment sketch** (`/opt/x.dufour.xyz/id.dufour.xyz/compose.yaml`):

```yaml
services:
  pocket-id:
    image: ghcr.io/pocket-id/pocket-id:v2.19.1@sha256:<digest>
    restart: unless-stopped
    env_file: .env                     # 0600: ENCRYPTION_KEY=<openssl rand -base64 32>
    environment:
      APP_URL: https://id.dufour.xyz
      TRUST_PROXY: "true"              # or the web subnet as CIDR
      ANALYTICS_DISABLED: "true"
    volumes: ["./data:/app/data"]      # SQLite; back up nightly with ENCRYPTION_KEY kept separately
    networks: [web]
    labels:
      traefik.enable: "true"
      traefik.http.routers.id.rule: Host(`id.dufour.xyz`)
      traefik.http.routers.id.entrypoints: websecure
      traefik.http.services.id.loadbalancer.server.port: "1411"
networks:
  web: { external: true }
```

- **First boot.** Complete the admin setup on the real domain immediately: the setup page is open to the first visitor.
- **In Pocket ID:**
  - create the groups `copycast` (and optionally `copycast-admins`);
  - add a "Copycast" client with redirect `https://copycast.dufour.xyz/auth/oidc/callback` and PKCE on;
  - turn email login codes off.
- **Patching:** high or critical advisories within 72 h.

**Still unverified** (to settle in the spike):
- Pocket ID's `amr` values, its SCIM behaviour on disable vs delete, its back-channel logout and its real RAM use;
- whether claude.ai sends `resource`;
- the hairpin from the Copycast containers;
- side effects of the NAT fix on the other stacks.

---

## 3. Multi-user model

### 3.1 Roles

**Admin** and **Member**. There is no guest role; a Listener role (subscribe and play only) is an open decision.

| | Admin | Member |
|---|---|---|
| Browse the Library, subscribe, play, Listening positions, own Feed keys | yes | yes |
| Create Mirrors (Library or Private) and Buckets; push Requests to own Buckets | yes | yes |
| Additive actions on a subscribed Library Mirror (archive on demand, retry, refresh) | yes | yes |
| Policy, Tombstones, `delete_feed` | any Feed | Feeds they own |
| Cookie file, Defaults, rebuild, purge, Users and Invites | yes | no |
| Captures | yes | not until per-user Cookie files |

Enforcement is **declared in the capability registry**, next to `DESTRUCTIVE`. For example, `@capability("update_mirror", …, access=Access(role=MEMBER, on="feed", needs="owner"))`. Every capability must declare `Access`, and the convergence test checks it. A Feed you cannot see returns **404, never 403**.

### 3.2 Per-user vs shared

| Resource | Decision |
|---|---|
| Library Mirrors | One per Source; `source_dedup_key` stays unique among Library Mirrors and the Mirror ID is unchanged. `create_mirror` on an existing Source becomes **create-or-subscribe** (`subscribed_existing: true`, the requested policy not applied, and the response says so). |
| Private Mirrors | Forced for Captures, for Source URLs with userinfo, and for query keys matching `auth\|token\|key\|secret\|sig`, using one predicate across create, retarget and the migration. New ids are salted with the Owner. Existing ids never change. This also fixes the `list=WL` collision between users. |
| Buckets | Owned by one User. **Each User gets their own default "Copycast" Bucket.** |
| Requests, jobs | Record `requested_by`. Jobs are visible when their Feed is visible; `requested_by` is hidden from non-owners. |
| API keys | Owned by a User. What a key can do is its Scope ∩ its User's role ∩ that User's ownership. No tool input has a `user_id` field. |
| Defaults | Global, Admin-only. |
| Cookie file | Chosen by the **acting/creating User**, not by visibility. The global file is the bootstrap Admin's. Members' Mirrors and probes run **without cookies**, and `WL`/`LL`/`LM` are refused for Members until per-user files exist. Otherwise a Member could mirror the Admin's Watch Later. |
| Plays (was Download count) | Global telemetry, counting unchanged (ADR 0010); web plays count too; attributed per Feed key and per User. |
| Listening position / Finished | Per User, per Episode. |
| SSE events | Filtered to visible Feeds; Feed-less events go to Admins only. A subscription-change NOTIFY recomputes the filter. The stream closes when its session is revoked. |
| About, OPML | About shows totals over the caller's Feeds; OPML lists the caller's Subscriptions with the caller's keys. |
| Last subscriber leaves | The Mirror is **Paused, never deleted** (ADR 0009). Ownership passes to an Admin. |

### 3.3 Threading "who is acting"

- `application/principal.py`: `Principal(user_id, role, via, scope, api_key_id, feed_key_id)`, with `Principal.SYSTEM` for the worker and Light Refresh.
- `get_services` (`adapters/api/deps.py`) returns `services_for(principal)`. Its unit of work scopes Feed lookups by owner, so most of the ~37 call sites do not change.
- MCP: `with_scope` becomes `with_principal`.
- Public feed routes authorise the **Feed**, then run as SYSTEM on the Owner's behalf, so on-demand archiving still works.
- Auth off means the implicit Admin, so **the existing test suite runs unchanged**. That is the compatibility test.

### 3.4 The "household" cut (not chosen)

The household alternative would have dropped the governance layer: no Owner, no 403 matrix, no create-or-subscribe, and everyone sees and manages every Feed. It would have saved about 8–12 person-days. **Decision D1 chose the full Library instead.**

---

## 4. What it means for secure URLs

### 4.1 Granularity

| Secret per… | Blast radius of a leak | Revocation | Verdict |
|---|---|---|---|
| Feed (1.3, ADR 0011) | One Feed, for every subscriber | Rotating breaks everyone | Cannot express multi-user |
| User (PodFetch, PinePods) | All of a User's Feeds | Breaks all of their apps | Too broad: Overcast's servers would hold the master key |
| **(User, Feed)**, optionally one per app | One Feed for one User | Independent, labelled ("Overcast", "iPad") | **Chosen** |

### 4.2 The URL scheme

A **Feed key** consists of a `key_id` (8 characters, the Basic username) and a `secret` (24 characters, ~119 bits) from `FEED_ALPHABET`. The link token is `{key_id}.{secret}`; `.` is not in the alphabet, so the split is unambiguous.

| | 1.3.1 today | Key pair (the 23 existing) | Key link (new, for Apple Podcasts & co.) |
|---|---|---|---|
| Feed | `https://hq7m…:x4tw…@copycast.dufour.xyz/feeds/3f2a9c1e7b4d8a60.xml` | **identical** | `https://copycast.dufour.xyz/feeds/k/hq7m….x4tw…/3f2a9c1e7b4d8a60.xml` |
| Enclosure | `…/feeds/3f2a…/media/9c0d….m4a` (bare; the app reuses the pair) | **identical** | `…/feeds/k/{token}/3f2a…/media/9c0d….m4a` |
| Chapters, transcripts, attachments | `…/feeds/3f2a…/assets/{name}` | identical | `…/feeds/k/{token}/3f2a…/assets/{name}` |
| Artwork, logo | public, bare | identical | **bare** (public, and the most redistributed URL) |
| Feed body | | adds `<itunes:block>Yes</itunes:block>` + `<podcast:block>yes</podcast:block>` | same |

Why this shape:
- **Everything stays under `/feeds/`.** A self-hoster behind any IdP needs exactly one bypass rule.
- `k` is reserved as a Feed id.
- No query-string tokens: they leak via Referer and logs, and some apps drop them.
- **Block tags:** Overcast treats a feed as private when it has Basic credentials *or* `itunes:block`. A link-form feed without the tag could surface in Overcast's search. Apple also recommends `itunes:block` for feeds followed by URL.

**Rendering.**
- The body is rendered once per `(feed_id, revision)` with two bases:
  - `feeds_base` = a per-process random sentinel origin, `https://copycast-{hex}.invalid`, for self, enclosures, chapters, transcripts and attachments;
  - `site_base` = the real `base_url`, for artwork, the logo and the channel `<link>`.
- Per request, `{feeds_base}/feeds/` is replaced with `{base_url}/feeds/` (pair) or `{base_url}/feeds/k/{token}/` (link). Show notes that contain Copycast URLs are untouched, because no Source can contain the sentinel.
- Tests:
  - the pair form is byte-identical to rendering with `base_url`;
  - `.invalid` never appears in any output;
  - goldens cover a Bucket and a synthesised Mirror in link form.

### 4.3 Lifecycle

| Action | Effect |
|---|---|
| Mint | Every Subscription gets a "Default" key. Users add labelled keys. |
| Replace | A new key; the old key gets `revoke_after = now + grace` (0 days by default, 7 optional). |
| Revoke | Immediate. Link form → 404 with no challenge (no oracle); pair form → 401 Basic. |
| Unsubscribe | A confirm dialog lists the keys with their last use ("Overcast used this 2 h ago"). Revokes them unless you choose "keep my keys". |
| Disable a User | Revokes all of their sessions, API keys and Feed keys. |

- `rotate_feed_credentials` retires in favour of "replace my default key".
- Rotation stays route-only and is never an MCP tool: an agent must not break a podcast subscription.

### 4.4 Leak handling

- **Per-key telemetry:** `last_used_at`, user-agent family, Plays.
- **Shared-link signal:** Overcast's crawler announces `Overcast/1.0 Podcast Sync (N subscribers; …)`. N > 1 on a personal key flags it as possibly shared. IP signals are useless here: Traefik sees every client as 172.19.0.1.
- **Copycast logs** redact `/feeds/k/***/…` and record `feed_key_id` (ADR 0013 amended).
- **Traefik's access log stores paths.** Before key links ship, drop `RequestPath` from the access log or treat the file as secret (0600 plus rotation). This is mandatory.
- **What a leaked key opens:** one Feed's RSS, media and non-artwork assets. Never `/api`, `/mcp` or another User.

### 4.5 Client compatibility

| App | Form |
|---|---|
| Overcast | Pair (today) or link. Ping does not support Basic feeds. |
| Apple Podcasts | **Link only.** It cannot download audio behind Basic. |
| Pocket Casts, Castro, Snipd | Link (Basic support is limited or unconfirmed) |
| AntennaPod, Podcast Addict | Either |

The UI shows two tabs: "Overcast & AntennaPod" (pair) and "Apple Podcasts & others" (link).

### 4.6 Migrating the 23 live subscriptions

1. Copy each `feeds.auth_username`/`auth_password` into `feed_keys(feed_id, key_id, secret, user=Admin, label="Original link (1.3)", is_default)`. The unique key is `(feed_id, key_id)`, so collisions across feeds are harmless.
2. Pair-form output equals 1.3 apart from the two block tags. Enclosure URLs are unchanged, so **nothing re-downloads**.
3. **No redirects.** A 301 to the link form would change the enclosures and trigger a mass re-download.
4. The legacy Operator pair keeps opening **`/feeds/*` only**, for one release, with every use logged as `auth.legacy_basic`. It is never accepted on `/api` from a browser, and it is refused once the Admin has a passkey. Otherwise the cached browser Basic would bypass the new login.
5. Test plan:
   - `test_existing_pair_fetch_is_unchanged`: enclosure URLs are compared byte for byte;
   - a dry run of the migrations on a production `pg_dump`;
   - `curl -u pair` after the deploy;
   - two hours of watching for `Overcast/1.0` 401s;
   - a two-account Overcast test (two pairs on the same Feed, revoke one). Whether Overcast deduplicates feeds by URL without credentials is **unverified**.
6. **Rollback runbook:**
   - each migration has a tested `downgrade()`;
   - the feed-keys downgrade writes the Admin's current default key back into `feeds.auth_*`;
   - `pg_dump` before the deploy;
   - `copycast db downgrade <rev>` with the new image, then swap back to the `.env.bak` image (an old image facing a newer schema fails at `ensure_schema`);
   - the Library migration is one-way once Members exist.

---

## 5. MCP

- **API keys get an owner** (`api_keys.user_id`, claim `copycast_user_id`). Members' keys are limited to their own Feeds.
- **Fixes in M0:**
  - the MCP resources `copycast://about`, `feeds` and `feeds/{id}` skip the scope check today (`adapters/mcp/resources.py`);
  - `list_feeds`/`get_feed` hand a `read` key every Feed's pair. After the fix they return the caller's own key.
- **Additive contract:** `feed_url` keeps its pair form, and `feed_link` (the key link) is added next to it.
- **New tools:**

  | Tool | Scope | Arrives in |
  |---|---|---|
  | `list_library` | read | 2.0 |
  | `subscribe_feed` | write | 2.0 |
  | `unsubscribe_feed` | full, destructive | 2.0 |
  | `list_listening_positions` | read ("what am I in the middle of?") | 1.5 |
  | `mark_finished` | write | 1.5 |

  These are tool-exempt: sessions, users, invites, passkeys, Feed-key minting and revocation, and the position heartbeat.
- **A Bearer key on `/api`** may call only tool capabilities; everything tool-exempt needs a browser session. Otherwise a key could mint keys.
- **OAuth for claude.ai and Desktop is deferred** (decision D5). When it matters, the cheapest route is fastmcp's `OIDCProxy` (CIMD+DCR toward Claude) inside `MultiAuth` with the existing keys, in front of an IdP: Pocket ID (CIMD-native, passkey-only) or Nextcloud's `oidc` app (no new service, but its DCR clients expire after 1 h). That is 5–6 pd plus the IdP. Claude's `static_headers` beta would make the existing keys work with zero effort, if your plan gets it.

---

## 6. Web player and UI/UX (same layout, more capability)

The plan keeps `PlayerBar.tsx` with its single `<audio>` and the store in `web/src/stores/player.ts`, and adds nothing that changes the visual language.

### 6.1 Tier 1: ships in 1.4, browser only

| # | Feature | Detail |
|---|---|---|
| P1 | **Speed slider, 0.5–3×** | The Gauge button opens a popover (a slider cannot live inside the current `role=menu` DropdownMenu). It holds a native `<input type=range step=0.05>`, which VoiceOver can adjust, preset chips `0.75 1 1.25 1.5 1.75 2 2.5 3`, and −/+ buttons. **Mouse-wheel/trackpad scroll on the control** moves ±0.05, which matches the "scroll bar" ask. `[`/`]` step ±0.1, clamped. The label reads "1.35×" with `aria-valuetext`. |
| P1 fix | **Rate-reset bug** | `readPrefs` accepts only `RATES` members (`player.ts:55-56`), so any slider value would reset to 1× on reload. Setting `src` runs the load algorithm, which resets `playbackRate`, and the rate effect does not re-run on a track change (`PlayerBar.tsx:76-104`). Fix: validate a range, set `defaultPlaybackRate` as well, and re-apply on `loadedmetadata`. |
| P2 | **Remember progress (local layer)** | `stores/listening.ts` (not "progress", which already means job progress): an LRU of 500 entries plus a snapshot of the current track. Writes every ~5 s and on pause, seek, ended, `visibilitychange` and `pagehide`. Resume at the saved position unless Finished, under 10 s, or within 30 s of the end. Auto-rewind of 3 s after 1 min, 10 s after 1 h, 20 s after a day. After a reload the bar comes back paused (autoplay rules). |
| P4 | Row progress | A 2 px bar under the title, "23 min left" or a check in Duration, and "Resume from 12:34" on Play. Also fixes the 4 Hz re-render of every row (`useIsPlaying` subscribes to the whole store). |
| P7 | Keyboard safety | Space no longer hijacks focused buttons or checkboxes; ↑/↓ act only inside the player; player keys are listed in the `?` dialog; a switch turns off single-letter keys (WCAG 2.1.4). |
| P8 | Bottom stack | The player covers the SelectionBar and the toasts, and the page padding of 64 px is less than the ~79 px bar. Fix with a `--player-h` variable and safe-area insets. |
| P9a | **Chapters fix (server)** | The engine writes yt-dlp's raw `[{start_time,…}]` (`archive_item.py:264-267`) but advertises Podcasting 2.0 JSON, so YouTube chapters work in neither the web player nor podcast apps. Fix: a pure `domain/chapters.py` converter, a tolerant parser, a one-off rewrite of the existing files, and a FakeEngine that emits chapters. |
| P10 | Media Session | Lock-screen and headphone controls, artwork (public, so the OS can load it), `setPositionState` that stays right at 3×. On iOS, seek handlers only by default; chapter next/previous goes behind a device test. |
| P11 | Sleep timer | 5–60 min, end of chapter or end of Episode. A fade on desktop; a plain pause on iOS, where volume is read-only. |
| P9b | Chapter list | A popover from the chapter title, prev/next buttons, Shift+←/→. |

### 6.2 Tier 2: 1.5, with server state

| # | Feature | Detail |
|---|---|---|
| P6 | **Web media route** | `GET\|HEAD /api/feeds/{feed_id}/items/{item_id}/media` and `/assets/{name}`: the same `FileResponse` code with `count=False, on_demand=False`, `Cache-Control: private`, and an **absolute** URL composed by the server (`media.play_url` and `AssetRead.play_url`). It must be absolute because `audio.src !== track.url` compares against an absolute URL; a root-relative URL would reload the element on every pause and lose the position. The player, chapters, transcripts, attachments and Download all use it. **Media requests count exactly as on `/feeds` today (decision D4)**, attributed to the User. This moves the browser off `/feeds/*`, which multi-user needs anyway. |
| — | **ADR 0010: decided, keep counting** | Web Play starts keep counting (`counts_as_download`: GET with no Range, or `bytes=0-`). "Download count" is renamed **Plays** everywhere, and the listening state is **Finished** (D4b). Accepted side effects: a short web preview starts the Bucket autoprune clock, web plays extend Automatic expiry, and a resume that re-requests `bytes=0-` counts again. |
| P3 | **Listening positions on the server** | `listening_positions(user_id → users, item_id, feed_id, position_s, duration_s, completed_at, client_at, updated_at, device)`, PK `(user_id, item_id)`. `users` arrives in the same migration with only the bootstrap Admin, so there is no `'operator'` string to rewrite later. `PUT …/listening` every ~15 s of play, immediately on pause and ended, and via `fetch(…, {keepalive:true})` on hide. **Latest `client_at` wins** (clamped to now+60 s). `ItemRead.listening` is inline. Safari wipes script storage after 7 days, so a local-only layer is not enough on an iPhone. |
| P5 | Continue listening | A group in ⌘K, and a strip at the top of the Feeds page. |
| P14 | Unfinished filter, Mark finished | Single and bulk (selection grammar, ADR 0008); "Mark all older as finished". Finished never touches archiving. |
| P13 | Playback preferences | Skip lengths, auto-rewind, finished threshold, per-feed speed. |
| P12 | Up Next | "Play next" / "Add to queue", auto-advance within a Feed. Kept locally at first. |
| P15 | Phones | A mini bar plus a bottom "Now playing" sheet (the existing `ui/sheet.tsx`) holding speed, sleep and chapters. 44 px targets. The volume slider is hidden on iOS. The desktop bar does not change. |
| P16–P18 | Accessibility and errors | The seek bar commits on `change`, so it works with VoiceOver and TalkBack. A live region, focus return and per-row labels. Precise playback errors and "Archive and play" through the job and SSE. |
| P19–P22 | Polish | Show notes with clickable timestamps; play from Requests; deep links `?item=&t=` (always to the UI, never to a feed or media URL); "−18:40 at 2.5×, ends at 14:32"; a PWA manifest. Also small fixes: `useSendToBucket` matches the name "copycast" instead of `is_default`, and `qrcode` uses caret pins against the pinning rule. |

### 6.3 Tier 3: later

- Finished-aware Bucket Retention, as an option.
- Cross-device Up Next.
- A transcript pane, which needs auto-captions enabled first.
- **Skip silence and loudness as an analysis asset computed once at archive time** (ffmpeg `silencedetect`/`ebur128`). The player jumps over gaps without Web Audio, so it works on a locked iPhone.
- SponsorBlock segments: skip only, never cut.
- gPodder v2 sync, only if someone uses AntennaPod or Kasts.
- Offline Episodes via a service worker.
- AirPlay picker; "Add to Overcast" button (unverified URL scheme).
- Listening stats ("time saved by speed").

### 6.4 Rejected

| Idea | Reason |
|---|---|
| Web Audio skip-silence or boost | Breaks in the background on iOS. |
| Rewriting archived media | Podcast apps download the same file. |
| Speed above 3× | Not asked for; Firefox mutes above 4×. |
| Inferring Finished from Plays | Plays are telemetry, not intent. |
| `sendBeacon` | Cannot PUT or set headers. |
| `?via=web` or header sniffing for counting | `<audio>` cannot send custom headers, and the browser would stay on the feed-secret paths. |
| Overcast progress import | Overcast has no API. |

---

## 7. Roadmap

| Release | Milestone | Contents | pd |
|---|---|---|---|
| **1.4.0** | Player quick wins + hardening | §6.1 (P1, P2, P4, P7, P8, P9a, P9b, P10, P11), plus Fetch-Metadata CSRF guard (closes body-less cross-site POSTs today), MCP resource scope fix, `Forbidden`→403 | 8–10 |
| **1.5.0** | Principal seam + Listening positions | `users` (bootstrap Admin only), `Principal`/`services_for`, `Access` on every capability (+ convergence checks), `api_keys.user_id`; web media/asset route + ADR 0010 amendment; `listening_positions`, Continue listening, Finished, Unfinished filter, `mark_finished` / `list_listening_positions` tools; the Download count → Plays rename | 10–13 |
| **1.6.0** | Feed keys | `feed_keys` + migration of the 23 pairs, key pair + key link, block tags, sentinel render, key UI (two tabs, labels, replace with grace, revoke, QR, last used, "possibly shared"), per-key OPML, log redaction + **Traefik access-log action**. **Apple Podcasts works.** | 6–8 |
| **2.0.0** | Accounts + Library (shipped together) | Sessions, login/logout, user menu, passkeys (mandatory for Admins only when `base_url` is https/localhost), invites, per-username backoff (no IP limits: everything is 172.19.0.1), CLI `copycast users add/reset/reset-passkeys/disable`; `owner_id`/`visibility`/`subscriptions`, create-or-subscribe, Private Mirrors, per-user default Bucket, SSE/job filtering, cookie-by-acting-user, user-namespaced localStorage (logout clears it), isolation matrix generated from the registry, rollback runbook | 19–26 |
| optional | Add-ons | claude.ai OAuth via `OIDCProxy` + Pocket ID/Nextcloud (5–6 + IdP); OIDC login adapter (3–4); per-user Cookie files and Member Captures (4–6); silence/loudness analysis (4–6); gPodder v2 (4–6) | — |

**Accounts and Library ship together.** Inviting Members before ownership exists would show them every Admin Feed.

**Numbering.** Migrations continue from `0011_live_status`:

| Migration | Contents |
|---|---|
| `0012_users_listening` | `users`, `listening_positions` |
| `0013_feed_keys` | `feed_keys` |
| `0014_accounts` | `sessions`, `passkeys`, `invites` |
| `0015_library` | `feeds.owner_id`, `visibility`, `subscriptions` |

ADRs continue from 0014:

| ADR | Decision |
|---|---|
| 0015 | Web playback goes through the API; it still counts, and Download count becomes Plays (amends 0010) |
| 0016 | Listening positions and Finished are per-User server state |
| 0017 | Accounts: Copycast owns identity (supersedes 0011 §1; amends 0004) |
| 0018 | Feed keys: per-(User, Feed) secrets in pair and link form (supersedes 0011 §2) |
| 0019 | The Library: shared Mirrors, Owners, Subscriptions, Private Mirrors (amends 0007, 0009) |

The AGENTS.md ADR index also needs 0013 and 0014, which it is missing today.

**New Language.**
- **User**, **Admin/Member**, **Owner**, **Library**, **Private Mirror**, **Feed key** (key pair / key link), **Session**, **Passkey**, **Invite**, **Principal** (code only), **Listening position**, **Finished**, **Plays** (replaces Download count).
- **Feed credentials** is retired.
- **Operator password** narrows to "the bootstrap Admin's password".
- "Subscription" collides with the _Avoid_ lists of Mirror and Follow: amend them (recommended), or pick another word.

---

## 8. Top risks

1. **An unscoped path leaks another User's Feed.** Candidates: the ~37 Feed lookups, the asset route's own unit of work, MCP resources, SSE, and jobs looked up by id. Mitigation: scoping by construction, 404 rather than 403, an isolation matrix generated from the registry with an MCP twin, and a lint banning raw `uow_factory` in `adapters/api`.
2. **Owning login security.** Mitigation: Admin passkeys, argon2id, `__Host-` cookie, CSRF guard, invite-only accounts, CLI recovery, and a `/security-review` before 2.0.
3. **Breaking the 23 Overcast subscriptions.** Mitigation: pairs copied verbatim, byte-identical pair form, dump dry run, canary curl, legacy Basic logged on `/feeds` only, rollback runbook.
4. **Tokens in URLs** (logs, Overcast's servers, copy-paste). Mitigation: per-(User, Feed) blast radius, labelled keys, replace with grace, redaction, block tags, the Traefik log action.
5. **Shared-Mirror policy conflicts.** One User's Rolling or Sync tombstones another User's listening. Mitigation: Owner-only policy, a confirm dialog showing the subscriber count, additive-only rights for subscribers. This risk disappears in the household cut.

---

## 9. Decisions needed

Recommended option first in each row.

| # | Decision | Options |
|---|---|---|
| D1 | Who are the users? | ✅ **Full Library with governance** (decided 2026-10-27) |
| D2 | Identity | ✅ **In-app accounts** (decided 2026-10-27, after the §2.3 deep-dive). OIDC (Pocket ID) stays a later add-on. |
| D3 | Archive model | ✅ **Shared Library + Private Mirrors** (follows from D1) |
| D4 | Web plays and Download count | ✅ **Keep counting** (ADR 0010 unchanged). In 1.5 the API/MCP field `download_count` becomes `plays`; `download_count` stays as a deprecated alias for one release, so agents and `web/openapi.json` consumers do not break. |
| D4b | The new name | ✅ **Plays + Finished**: "Download count" → **Plays** everywhere (UI, API, MCP, ADR 0010, AGENTS.md; drop "Plays" from the Avoid list). The listening state is **Finished** (Avoid: played, listened, completed). (decided 2026-10-27) |
| D5 | claude.ai OAuth | **Defer** · `OIDCProxy` + Pocket ID · Copycast as its own authorization server |
| D6 | Feed key form shown first | **Pair (Overcast) and link (Apple) as two tabs** · Link everywhere |
| D7 | Server Listening positions | ✅ **In 1.5, keyed by `users.id`** |
| D8 | Per-feed default speed | **Tier 2 server preference** · Browser-only toggle now · Never |
| D9 | Persist Finished across `copycast rebuild` | **Yes, `data/listening/{user}.json`** · No (like API keys) |
| D10 | Legacy Operator Basic | **`/feeds/*` only, one release, logged** · Keep as an opt-in flag for scripts |

---

## Appendix: verified code facts

| Fact | Location |
|---|---|
| Authentication is innermost ASGI middleware; `is_gated` exempts health, `/mcp`, robots and `/feeds/` | `adapters/api/auth.py:84-88, 113-137`; `app.py:190-200` |
| Feed pairs are checked per request, in constant time after a database read | `auth.py:140-165` |
| Artwork is public | `auth.py:91-97` |
| All published URLs are composed in one place | `adapters/feeds/urls.py` |
| The SPA has no login code; UI, SSE and `<audio>` rely on the browser's cached Basic credentials | `web/src/api/client.ts`, `events.ts:234`, `RowActions.tsx:76` |
| `RATES` stop at 2× and `readPrefs` requires membership | `web/src/stores/player.ts:38, 55-56` |
| The `src` comparison runs inside the `[track, playing]` effect; the rate effect does not depend on the track | `PlayerBar.tsx:76-104` |
| `counts_as_download` counts a GET with no Range or with `bytes=0-` | `routes/public.py:89-99` |
| Autoprune requires `first_downloaded_at` | `repositories/catalog.py:375-379` |
| Engine chapters are written raw | `worker/jobs/archive_item.py:264-267` |
| `useSendToBucket` matches the Bucket by name | `useSendToBucket.ts:9` |
| MCP resources skip the scope check | `adapters/mcp/resources.py` |
| A `read` key gets every Feed's pair | `adapters/mcp/tools.py:156-162` |
| `uvicorn.run` does not set `forwarded_allow_ips` | `cli.py:88` |
| Mirror id derivation | `sha256(dedup_key)[:16]`, `domain/ids.py:36` |
| The `k` id is accepted today | `FEED_ID_RE`, `domain/ids.py:15` |
| Latest migration | `0011_live_status`; ADRs end at 0014 |

**Unverified; needs a browser or device:**
- the rate reset after `src` assignment in each engine;
- iOS lock-screen seek vs track buttons;
- iOS `src` swap while locked;
- whether resuming mid-file still requests `bytes=0-`;
- Apple Podcasts "Follow by URL" with a blocked feed;
- whether Overcast keeps two Users' pairs for the same Feed apart.

**Sources** (research, Oct 2026):
- [Authelia 4.39 release notes](https://www.authelia.com/blog/4.39-release-notes/), [Authelia OIDC roadmap](https://www.authelia.com/roadmap/active/openid-connect-1.0-provider/)
- [Pocket ID CIMD](https://pocket-id.org/docs/guides/client-id-metadata-documents), [Nextcloud oidc app](https://github.com/H2CK/oidc)
- [Overcast podcaster info](https://overcast.fm/podcasterinfo), [Apple: distributing private shows](https://podcasters.apple.com/support/5108-how-apple-podcasts-distributes-your-shows-to-listeners)
- [iOS Media Session notes](https://dbushell.com/2023/03/20/ios-pwa-media-session-api/)

The raw research and design reports (5 maps, 3 designs, judgments, critique) are kept in the session scratchpad.
