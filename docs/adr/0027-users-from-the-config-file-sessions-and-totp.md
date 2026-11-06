# 0027 Users come from the config file; the browser signs in to a session, with TOTP for Admins

Status: accepted (3.0.0); supersedes ADR 0011 §1 (the operator password on the web UI and the
API) and amends ADR 0004 (open mode is one implicit Admin); ADR 0011's feed credentials and
API keys stand

## Context

Copycast had one credential for people: the operator password of ADR 0011, sent as HTTP Basic
on the web UI, the API and every feed. 3.dufour.xyz now has a second person on it, and the
configuration and users plan (decisions D1 to D3) asked for private Feeds per User, a login
that is more than a password for the Admin, and no user-management UI to build and secure.
Basic auth also has no way to sign out, no throttling of its own and nothing to hang a second
factor on, and the browser resends the pair on every request, including the cross-site ones
ADR 0025 had to refuse.

## Decision

**Users are configured, never created through the API.** `[[auth.users]]` in `copycast.toml`
(or `COPYCAST__AUTH__USERS` as JSON, which replaces a TOML list and never merges with it)
lists each User: `username` (1 to 32 of `a-z 0-9 . _ -`, starting with a letter or digit),
`password_hash`, an optional `totp_secret`, `admin` and `quota`. A username is the stable
identity: renaming a user creates a new one. The table `users` only mirrors the configured
names at startup, never deletes one, and holds no secret.

- `password_hash` must be an argon2id PHC string; anything else, a plaintext password
  included, stops startup with exit 2. `copycast users hash-password` prints one (argon2id,
  t=3, m=64 MiB, p=4) and `copycast users totp NAME` prints a secret, its `otpauth://` URI
  and a QR code. Both are CLI only and need no database.
- Validation at startup: unique names, at least one Admin, quotas that parse, an
  `owner_of_existing` that names a configured user, and `auth.password` and `auth.users`
  exclusive. A global `[engine.options] cookiefile` is refused alongside users (Cookie files
  are per User, ADR 0028).
- Roles and quotas are read from the configuration on every request, never from Postgres,
  so a demotion or a new quota applies at the next restart.

**Three auth modes.** *Open* (no users, no password) is ADR 0004 unchanged: every request
acts as one implicit Admin named after `auth.username` (`copycast` by default), and 3.0 is
byte-for-byte 2.3.0 there. *Legacy* (`COPYCAST__AUTH__PASSWORD` only) keeps the operator
password as a single Admin, now signing in through the login page. *Users* is `auth.users`
non-empty. In legacy and open mode the owner of everything is `owner_slug(auth.username)`
(lower-cased, else slugged), so a 2.3.0 name such as `Florent` or `me@example.org` keeps
working.

**The browser signs in to a session.** `POST /auth/login` takes a username, a password and,
for a User who has a TOTP secret, a 6-digit code, and answers 204 with a cookie:
`__Host-copycast_session` with `Secure` when `base_url` is https, `copycast_session` without
it on plain http; both `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` of `session_days`.
The session id is 32 random bytes, stored as its SHA-256. It slides: `last_seen_at` and
`expires_at` are written at most once a minute, and `GET /api/me` re-issues the cookie.
`POST /auth/logout` deletes the row and always answers 204. A login deletes the session that
came with it, so an injected or stale cookie never survives a sign-in. On an https site, a
login over plain http is refused (`400 https-required`): the browser could not store the
cookie and the sign-in would seem to work and not stick.

**`/api` takes sessions and Bearer keys, never Basic.** One ASGI middleware (`PrincipalMiddleware`,
where `OperatorAuthMiddleware` was) resolves a **Principal** for each request: open mode's
Admin; else the session cookie on any path; else, under `/api/` only, an API key. A cookie that
names no session falls through to the key. Without a Principal, `/api` answers 401
problem+json with no `WWW-Authenticate` challenge, so the browser shows the login page
instead of its Basic prompt. The login page, the SPA shell and its assets, `/healthz`,
`/uploads/{id}/{token}`, `/art/` and `/feeds/` stay outside it, each behind its own credential.
A key on `/api` reaches tool capabilities only, scoped by method (`read` for GET and HEAD,
`write` otherwise, `full` for destructive ones); `get_me`, the key, cookie and Defaults routes
and the web player's routes refuse keys. The argument-level checks a key's scope implies
(Rolling or expiring backfills, `engine_options`, `autoprune_days`) moved from the MCP tools
into the services, so `/api` and MCP enforce them alike.

**TOTP for Admins on https.** RFC 6238 with SHA-1, 6 digits, 30 s, one step either side,
hand-rolled in `domain.totp` on the standard library; a code is good once (the matched step is
claimed per User). In users mode an Admin without a `totp_secret` on an https site cannot sign
in (one generic 401, `auth.login_refused reason=admin_without_totp` in the log, and a startup
warning); their API keys keep working, being separate 256-bit credentials. A Member's TOTP is
optional. The legacy single Admin is not required to have one: requiring it would lock out
every https self-hoster on upgrade, so startup warns instead.

**Throttling per username, atomic, with no oracle.**
- Every login answers an unknown name, a wrong password, a missing or wrong code and an Admin
  without TOTP with the same 401 body, and runs exactly one argon2 verify: a dummy one with the
  slowest configured parameters for an unknown name. Hashes with different parameters answer
  at different speeds, so startup warns (`auth.hash_parameters_differ`).
- argon2 runs on its own limiter: two verifies at a time per process (2 × 64 MiB), at most 16
  logins waiting for up to 5 s, then `503 login-busy` with `Retry-After: 5` before any verify
  and without counting a failure. It never uses the default thread limiter that FastAPI's sync
  dependencies and `FileResponse` share.
- Holding its slot, a login claims its attempt in one locked row (`login_attempts`, read
  `FOR UPDATE`): a locked name is refused with nothing counted (`429 login-locked`,
  `Retry-After`); otherwise the attempt is counted as a failure before the verify and a
  success resets it. After 5 failures the lock is 30 s, doubling per further failure, capped
  at 15 minutes. So a burst of guesses, from any number of api processes, never verifies
  more than the threshold allows.
- A typed name that is no User is counted under `"?" + HMAC-SHA256(instance secret, name)`,
  never in clear, so a password typed into the username field is not stored readable. Stale
  rows are pruned at startup and every 10 minutes.
- There is no per-IP limit: behind Traefik every client has the proxy's address. The
  trade-off is accepted: anyone who knows a username can keep it locked with one wrong attempt
  every 15 minutes. A lock only delays, sessions that exist are untouched, and an operator
  clears it by deleting the user's `login_attempts` row.

**A credential change signs the user out.** `users.credential_digest` stores, per User, a
SHA-256 of the configured hash and TOTP secret (not a secret itself). When it differs at
startup (a new password, a new TOTP secret, the first start of 3.0), every session of that
User is deleted. In legacy mode the digest is a salted argon2id hash of the operator
password, never a fast digest of it, computed once at startup on the hash limiter. Sessions of
users no longer configured, and expired sessions, are deleted at every start.

**The operator pair on `/feeds`.** Legacy mode keeps it: `auth.username` and `auth.password`
still open every feed, as in 2.3.0, so podcast apps subscribed with it keep working. Since
the operator password is also the legacy Admin's sign-in password, a pair naming
`auth.username` claims an attempt on that name's `login_attempts` row before the password is
compared (3.0.0-rc.1): the lockout covers `/feeds` too, and a locked name refuses even the
right pair. Users
mode drops it: there is no plaintext password to compare, and an argon2 verify per feed fetch
would be a CPU sink. A feed opens with its own pair or media key (ADR 0011, ADR 0020), and
`/feeds/{id}.xml` also with the session of its owner or of an Admin.

**Data changes owner only by an explicit command.** Migration 0017 gives every existing Feed and
API key to `owner_of_existing` (users mode: that setting, else the first Admin;
legacy and open mode: the legacy owner), passed by every process that migrates. Two traps
follow, and neither is fixed automatically:
- *The mode switch.* A self-hoster who upgrades in open or legacy mode has their Feeds owned by
  the legacy owner (`copycast` by default). Switching to users mode later without configuring
  that name makes every Feed "owner removed": its pairs, media keys and API keys answer 401.
- *The legacy rename.* Renaming `auth.username` (which 2.3.0 allowed freely), or setting a
  password with another username after an open-mode upgrade, changes the legacy owner with the
  same effect, without leaving legacy mode; `owner_of_existing` cannot help there (it must
  equal the legacy owner).

Startup logs `auth.removed_owner_has_feeds` for every owner that has Feeds and is not
configured, with the Feed count and the fix: configure the name, or run
`copycast users reassign OLD NEW` (legacy mode: set `auth.username` back, or reassign to the
current name).

## Consequences

- Breaking: Basic auth on `/api` is gone in every mode. Scripts mint an API key (and then
  reach tool capabilities only) or sign in. API keys on `/api` routes that are not tools
  answer 403.
- Breaking, users mode only: podcast apps subscribed with the operator pair get 401 and need
  the Feed's own URL, which the UI shows.
- Removing a user from the file takes effect at the next restart: their sessions, keys and
  feed pairs stop working, their Feeds stay (Admins see them as "owner removed", ADR 0028),
  and adding them back restores everything.
- Replacing a password, or a TOTP secret, signs that user out everywhere at the next restart,
  which is how the sample passwords on 3.dufour.xyz are retired.
- The configuration file now holds secrets (TOTP secrets, offline-attackable hashes): it is
  mounted read-only and kept `0640 root:<container group>`. Hashes contain `$`, so they belong
  in the TOML file, never in `.env` or compose YAML where `$` interpolates.
- `login_attempts` holds up to a day of distinct unknown names (as HMACs), bounded by the hash
  rate.
- Not built: passkeys, a TOTP enrolment UI, password change or reset in the UI, a user
  management UI, per-IP throttling and a global login rate limit beyond the argon2 limiter.
