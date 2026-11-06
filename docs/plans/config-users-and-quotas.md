# Plan: users from the config file, storage quotas, and a security pass

Status: **draft, decisions D1–D4 taken 2026-11-05**. Based on `main` at `cbecdd5` (2.2.1). Nothing is built yet.

This plan **supersedes §2 (identity), §3 (multi-user model) and §4 (secure URLs) of
`multi-user-and-player.md`**. The player sections of that plan (§6) still stand. The 1.4 player work is already on `main`.

## 1. Decisions

| # | Decision | Choice (2026-11-05) |
|---|---|---|
| D1 | Sharing model | **Private per user.** Each User owns their Feeds. Another User's Feed answers 404. Admins see and manage every Feed. Two Users mirroring the same Source keep two copies. The shared Library of the 27 Oct plan is dropped for now. |
| D2 | At quota | **Block new downloads.** Running jobs finish, even if they overshoot. New archives, uploads and first plays of unarchived Automatic Episodes are refused as "Over quota". Nothing is deleted on the User's behalf. |
| D3 | Login strength | **Password (argon2id hash in the config) plus optional TOTP** (`totp_secret` in the config). No enrolment UI, no passkeys. |
| D4 | Feed and media URLs | **Harden and keep the URL shape.** `user:pass@` feeds and keyed enclosures stay byte-identical, so Overcast re-downloads nothing. Artwork moves to opaque tokens. Feeds get block tags. Responses give no existence oracle. Logs redact secrets. Apple Podcasts stays unsupported (key links are a later add-on). |
| D5 | Settings and cookies | **Each User has their own settings, including cookies.** Defaults (language, minimum length, default policy, refresh interval) and the Cookie files are per User. A User's Mirrors, probes and Captures use only that User's cookies. Members can make Captures with their own account. See §4.1. |
| D6 | Release shape | **2.3.0 first** (the security pass items that need no users, §7 marked *2.3*), **then 3.0.0** (users, sessions, ownership, per-user settings, quotas). |

Defaults applied without asking (change any of them before the build):
- Usernames are the stable identity. Renaming a user creates a new user.
- `password_hash` must be a PHC argon2id string. A plaintext password in the file is refused at startup. `copycast users hash-password` and `copycast users totp` (which prints the secret and a QR) are CLI-only.
- TOTP is **required for Admins** once `base_url` is https. Without it, startup logs a warning and Admin login is refused.
- The release is **3.0.0**: the login page replaces browser Basic auth, a breaking change for scripts that use the operator pair on `/api`.

## 2. Configuration

```toml
[auth]
default_quota    = "10 GB"        # decimal units; "unlimited" allowed
session_days     = 30             # sliding expiry
owner_of_existing = "florent"     # who owns the Feeds that exist before 3.0 (default: first admin)

[[auth.users]]
username      = "florent"
password_hash = "$argon2id$v=19$m=65536,t=3,p=4$…"
totp_secret   = "JBSWY3DPEHPK3PXP"   # base32; optional for Members
admin         = true
quota         = "unlimited"

[[auth.users]]
username      = "emilia"
password_hash = "$argon2id$…"
```

- The list can also come from the environment as JSON (`COPYCAST__AUTH__USERS='[…]'`), because production has no `copycast.toml` today. Recommended: mount a `copycast.toml` read-only at `/config` (the compose file already mounts `./config`) and keep secrets out of `.env`.
- **Auth is on** when `auth.users` is non-empty. The legacy `COPYCAST__AUTH__PASSWORD` alone still works and becomes a single Admin named `auth.username`, so other self-hosters do not break. With neither setting, ADR 0004 applies unchanged: one implicit Admin, and the existing test suite runs as is.
- Startup validation:
  - usernames are unique and match `^[a-z0-9][a-z0-9._-]{0,31}$`;
  - quotas parse;
  - at least one Admin;
  - `owner_of_existing` names a configured user.
- **Removing a user from the file** takes effect at the next restart:
  - their sessions, API keys and feed pairs stop working;
  - their Feeds stay in the database and the Object store, and Admins see them as "owner removed";
  - adding the user back restores everything;
  - no data is deleted automatically.

## 3. Identity and sessions

- **Database tables:**
  - `users(username PK, first_seen_at, last_login_at)` is synced from the config at startup and holds no secrets;
  - `sessions(id_hash PK, username FK, created_at, last_seen_at, expires_at, user_agent)`;
  - `login_attempts(username, failed_count, locked_until)`.
- **Login:** `POST /auth/login` takes `{username, password, totp?}` and returns 204 with `Set-Cookie: __Host-copycast_session=…; Path=/; Secure; HttpOnly; SameSite=Lax`. The session id is 32 random bytes, stored as a SHA-256 digest.
  - Sessions slide for `session_days`.
  - `POST /auth/logout` deletes the session row.
  - On plain http (localhost dev), the cookie drops `__Host-` and `Secure`.
- **Throttling** is per username: after 5 failures, an exponential lock (30 s doubling, capped at 15 min). No IP limits, because Traefik shows every client as 172.19.0.1. The response and its timing are identical for an unknown user and a wrong password (a dummy argon2 verify runs).
- **TOTP:** RFC 6238, ±1 step, and a code is single-use. The last accepted step is stored per user.
- **A `Principal(username, admin, via, api_key_id)`** is resolved by one ASGI middleware:
  - from the session cookie in the browser;
  - from a Bearer API key on `/mcp`, and on `/api` for tool capabilities only;
  - `Principal.SYSTEM` for the worker and Light Refresh.

  `get_services` returns `services_for(principal)`.
- **SPA:** an unauthenticated `/api/*` call answers 401 problem+json **without** a `Basic` challenge. The app then shows `/login`. The user menu in the header shows the username and has "Sign out". localStorage keys are namespaced per user, and signing out clears them.

## 4. Ownership and roles (D1)

- **Migration `0017_users_and_owners`** (0016 is 2.3.0's `instance_secrets`):
  - `feeds.owner` (FK `users.username`, NOT NULL after backfill to `owner_of_existing`);
  - `api_keys.owner`;
  - `uploads.owner`;
  - `jobs.requested_by`.
- **Scoping by construction:** the unit of work from `services_for(principal)` filters every Feed lookup by owner, unless the principal is an Admin. That covers the 9 `uow.feeds.get/list` call sites, the asset route, MCP resources (which also fixes their missing scope check), SSE and jobs. A lint bans raw `uow_factory` in `adapters/api`. A Feed you cannot see answers **404, never 403**.
- **Mirror ids:** `mirror_id` stays `sha256(dedup_key)[:16]` for the first owner of a Source. A second User's Mirror of the same Source gets `sha256(owner + "\n" + dedup_key)[:16]`. Existing ids never change. `source_dedup_key` becomes unique per owner.
- **Default Local feed:** each User gets their own "Copycast" Local feed (`is_default` per owner).
- **Roles:**

  | Action | Admin | Member |
  |---|---|---|
  | Own Feeds: create, policy, delete, uploads, API keys | yes | yes |
  | Other Users' Feeds | yes (labelled with the owner) | 404 |
  | Own Settings: Defaults and Cookie files (§4.1) | yes | yes |
  | Captures (Watch Later, private playlists), with their own cookies | yes | yes |
  | Engine page (yt-dlp version), purge, rebuild, per-Mirror `engine_options` | yes | no |

- **API keys:** a key acts as its owner. What it can do is its scope ∩ its owner's role ∩ its owner's Feeds. No tool takes a `user_id`. Key minting stays session-only.
- **The capability registry** declares `Access` per capability (`member`, `admin`, `owner_of_feed`), and the convergence test checks that every capability has one. An isolation matrix generated from the registry runs every capability as Member A against Member B's Feed, for routes and MCP twins alike, and expects 404.

### 4.1 Per-user settings (D5)

- **Storage:**
  - Defaults move from `engine/defaults.json` to `users/{username}/defaults.json`;
  - Cookie files move from `engine/cookies.txt` and `engine/cookie-files/*` to `users/{username}/cookies.txt` and `users/{username}/cookie-files/*`;
  - `copycast rebuild` restores them per user.
  - The migration moves today's files to `owner_of_existing`.
- **A new User** starts from the built-in Defaults with no cookies.
- **The engine gateway** no longer holds one cookie path. Every engine call (list, probe, archive, playlist listing) takes the cookie file of the Feed's owner, or of the acting User for a probe. The Scratch copy is per job, as today.
- **Cookie files stay write-only through the API:** a status ("set, 12 cookies, uploaded 3 Oct") and a delete. Their content is never returned, not even to Admins.
- **Mirror ids:** a Capture's id depends on the owner too (the salting rule in §4 applies), so two Users' Watch Later never collide.
- **Global settings that stay global and Admin-only:**
  - the engine and its `engine.options` from the config;
  - refresh concurrency and cooldown;
  - storage;
  - the cache;
  - purge and rebuild.
- **Settings page:** it shows the caller's own Defaults and Cookie files. Admins additionally see the Engine section.

## 5. Quotas (D2)

- **Usage** is `sum(feeds.storage_bytes)` over the User's Feeds: media plus archived assets, already maintained per Feed. Scratch, the Feed cache and Postgres are not counted.
- **`quota_status(username)`** returns `{used_bytes, quota_bytes|null, state: ok|near|over}`, where `near` means ≥ 90% and `over` means ≥ 100%.
- **Enforcement** is a check before work starts:
  - `archive_item` jobs: when the owner is `over`, or when `used + estimate > quota`, the item goes to a new `blocked_quota` reason instead of failing. The estimate is yt-dlp's `filesize`/`filesize_approx`, else 0. Blocked items retry automatically when usage drops (the prune job or a delete re-queues them).
  - Uploads: `create_upload` declares a size, and `PUT` stops at the remaining quota. The answer is 507 Insufficient Storage problem+json, slug `over-quota`.
  - First play of an unarchived Automatic Episode: 507 to podcast apps (not 503 Retry-After, which would retry forever).
  - Backfill, Follow and policy switches queue as usual. Blocked items show "Over quota" in the Catalog.
  - A job already running always finishes. The overshoot is at most one Episode.
- **Nothing is deleted automatically.** Autoprune and Automatic expiry work as today and free space.
- **API:** `GET /api/me` returns `{username, admin, quota}`. `GET /api/status` gains a `storage` component for the caller, and for an Admin one fact per user. Both are routes only, with no MCP tool (`/api/me` is session data). `storage_summary` is scoped to the caller's Feeds.

## 6. Status popover

The header dot's popover gets a **Storage** row:
- "3.2 GB of 10 GB", with a bar and a percentage;
- the tone is `warn` at ≥ 90% and `error` at ≥ 100%, which turns the dot amber or red like any other component;
- "Unlimited" shows the used size only.

For Admins, the row lists each user (like the crawler list), including users removed from the config who still own Feeds.

## 7. Security pass

### 7.1 Boundaries

| Path | Credential |
|---|---|
| `/healthz`, `/robots.txt` | none |
| `/login`, the SPA shell and its hashed static files, `POST /auth/login` | none (the shell holds no data) |
| `/uploads/{id}/{token}` | the one-time token (192 bits) |
| `/mcp` | Bearer API key |
| `/feeds/{id}.xml` | the Feed's pair (Basic), or an Admin session in the browser |
| `/feeds/{id}/k/{key}/media|assets/…` | the media key in the path |
| `/feeds/{id}/media|assets/…` (bare) | the Feed's pair |
| `/art/{asset_id}-{mac}.{ext}` (new, public artwork) | none: the HMAC is the credential |
| everything else (`/api`, SSE, the SPA's data) | session (or API key for tool capabilities) |

### 7.2 Findings and fixes

| # | Finding | Fix |
|---|---|---|
| U1 | **Artwork is public at a computable URL.** `/feeds/{sha256(channel URL)[:16]}/assets/feed.artwork.jpg` and `{item}.artwork.jpg` let anyone confirm that the server mirrors channel X and archived video Y. Watch Later Captures have the same id on every install. | Public artwork is served at `/art/{token}.{ext}`, where `token = base32(HMAC(server_secret, asset_id))[:26]`. The old public artwork route answers 404 to anonymous requests. The feeds re-render (Feed cache keyed by version), and only image URLs change, which Overcast refetches without re-downloading audio. `server_secret` is 32 random bytes stored in the database at first start (the new `instance_secrets` table), so nothing new goes in the config. |
| U2 | Feeds can be found by directories | `<itunes:block>Yes</itunes:block>` and `<podcast:block>yes</podcast:block>` in every authenticated feed. |
| U3 | Existence oracles | Every `/feeds/*` route answers the same 401 (feed) or 404 (keyed path) for an unknown feed, an unknown item and a wrong secret. `require_feed_access` already does this for feeds; the keyed route and the asset route are made uniform too. |
| U4 | Secrets in logs | Copycast already logs `***`. Also redact the pair from `feed_url` in log events. **Ops:** drop `RequestPath` from Traefik's access log for the copycast router, or chmod 0600 and rotate it. |
| U5 | **SSRF (SEC-4)**: a Member can make the server fetch `http://seaweedfs:8333/…`, which holds every User's files | One fetch guard for probe, directories, the Atom feed, attachments and RSS sources: resolve the host and refuse private, loopback, link-local and CGNAT addresses (and redirects that lead to them). yt-dlp gets the same check on the Source URL. Admins can allowlist hosts. |
| U6 | **`netrc_cmd` and friends (SEC-1)** | Per-Mirror `engine_options` become admin-only, and the deny-list becomes an allowlist of known-safe keys. |
| U7 | **SeaweedFS has no authentication (SEC-2)** | Bind master, volume and filer to the compose network only. Verify on 3.dufour.xyz that no port is published. S3 keys are already required on the S3 gateway. |
| U8 | CSRF | Cookie sessions make this mandatory. Middleware refuses unsafe methods unless `Sec-Fetch-Site` is `same-origin`/`none`, or, without Fetch Metadata, unless `Origin` matches `base_url`. SameSite=Lax is defence in depth. |
| U9 | No security headers | The SPA and `/api` get `Content-Security-Policy` (`default-src 'self'; img-src 'self' data: https:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'`), `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, and `Strict-Transport-Security` when `base_url` is https. Feeds and media get `Referrer-Policy` and `nosniff` only. |
| U10 | The browser on `/feeds/*` | The web player moves to `GET /api/feeds/{id}/items/{item}/media` (the 27 Oct plan's P6, session cookie, counts as a Play). The browser never needs a feed secret. |
| U11 | Forwarded headers | Set `forwarded_allow_ips` to the proxy network, so the request log and `Secure` cookie decisions see https. |

Already sound and unchanged: feed pairs (119 bits), media keys (110 bits), upload tokens (192 bits), API keys stored as SHA-256 digests, and constant-time comparisons.

## 7.3 What 2.3.0 actually built (2026-11-05)

- **Built as specified:** U1–U4, U6 and U9. U6's admin-only part waits for 3.0; until then, MCP needs a `full` key to set `engine_options`.
- **U5 goes further than planned.** It also guards yt-dlp's own connections (`adapters/engine/netguard.py` patches yt-dlp's urllib, requests and websockets handlers, and unregisters curl_cffi). ffmpeg run as a downloader is still outside the guard.
- **U8 adds a Host check** against DNS rebinding (`COPYCAST_ALLOWED_HOSTS`).
- **U9 adds `Content-Security-Policy: sandbox`** on `/feeds/` and `/art/` for everything but audio and video, against HTML or SVG assets served from Copycast's origin.
- **U7, decided by the user on 2026-11-05: no `nc` relay.** Master, volume and filer listen on loopback. The S3 gateway (8333, plus its gRPC on 18333, which takes IAM calls without a credential in SeaweedFS 4.48) listens only on an internal `objects` network shared with the api and the worker. gRPC mTLS is a follow-up.

## 8. Work breakdown (estimates)

| Step | Contents | pd |
|---|---|---|
| A | Settings: `auth.users`, quotas, CLI `hash-password` and `totp`, validation | 1–1.5 |
| B | Sessions, login, logout, TOTP, throttling, Principal middleware, CSRF, headers | 3–4 |
| C | Ownership migration, scoped unit of work, `Access` in the registry, isolation matrix, Mirror id salting, per-user Local feed, API key owners, SSE and job filtering | 5–7 |
| C2 | Per-user Defaults and Cookie files (§4.1): storage layout move, the engine gateway takes a cookie path per call, rebuild, Settings page | 2–3 |
| D | Quotas: `quota_status`, job/upload/on-demand enforcement, `blocked_quota`, re-queue | 2–3 |
| E | URL hardening U1–U4, the web media route U10, SSRF guard U5, engine_options U6, compose U7 | 3–4 |
| F | Web: login page, user menu, 401 handling, Storage row, Over quota badges, Admin owner labels | 2–3 |
| G | ADRs, AGENTS.md language, docs/operations.md (config users, Traefik log), e2e (two users), `/security-review`, 3.0.0 bump | 1.5–2 |

Total: about **20–28 pd**.

**Release split (D6):**
- **2.3.0** (about 3–4 pd):
  - U1 opaque artwork;
  - U2 block tags;
  - U3 uniform answers;
  - U4 log redaction;
  - U5 SSRF guard;
  - U6 the `engine_options` allowlist (admin-only arrives with roles in 3.0);
  - U7 SeaweedFS bind;
  - U8 CSRF guard on the current Basic auth, which closes cross-site POSTs now;
  - U9 headers;
  - U11 forwarded headers.
- **3.0.0:** A, B, C, C2, D, F, U10 and the rest of G.

**New ADRs** (2.3.0 takes 0022–0026, so 3.0 continues from 0027):
- 0027: users come from the config file; sessions and TOTP (supersedes ADR 0011 §1, amends 0004);
- 0028: Feeds are private to their owner (drops the Library);
- 0029: storage quotas;
- (public artwork under opaque tokens shipped in 2.3.0 as ADR 0022).

**New Language:** User, Admin, Member, Owner, Session, Quota, Over quota. The **Operator password** becomes the legacy single-Admin password.

## 9. Rollout on 3.dufour.xyz

1. `pg_dump` and keep `.env.bak-2.2.x`.
2. Write `copycast.toml` with the users. Mount it at `/config`.
3. Deploy 3.0.0. The migration gives every Feed to `owner_of_existing`.
4. Check with `curl -u pair` on two Feeds and an enclosure HEAD, then watch for two hours for `Overcast/1.0` 401s.
5. Ops: change the Traefik access-log setting (U4) and check SeaweedFS ports (U7).

## 10. Open questions

None blocking. Q1 (cookies) became D5. Q2 (release shape) became D6.
