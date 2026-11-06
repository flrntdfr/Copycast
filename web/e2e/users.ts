/**
 * The Users of the e2e stack (tests/e2e/config/copycast.toml, users mode): `admin` is an Admin,
 * `alice` and `bob` are Members, and bob's quota is 0, so he is Over quota from the start. TEST
 * VALUES ONLY: each signs in with `<username>-e2e-password`. The `setup` project signs each one
 * in once and keeps the session in `e2e/.auth/<username>.json` (git-ignored), which a spec picks
 * with `test.use({ storageState: authFile("alice") })`; the default is the Admin's.
 */
import { fileURLToPath } from "node:url";

export const USERNAMES = ["admin", "alice", "bob"] as const;
export type Username = (typeof USERNAMES)[number];

export const ADMIN: Username = "admin";

export function passwordOf(username: string): string {
  return `${username}-e2e-password`;
}

/** Where the `setup` project stores a User's signed-in browser state. */
export function authFile(username: Username): string {
  return fileURLToPath(new URL(`./.auth/${username}.json`, import.meta.url));
}

/** No cookies at all: a visitor who never signed in. */
export const SIGNED_OUT = { cookies: [], origins: [] };
