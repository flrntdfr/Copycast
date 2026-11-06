/**
 * Per-user localStorage keys. Everything a User leaves in the browser (player preferences and
 * track, listening positions, catalog columns, the Buckets box) lives under
 * `copycast.u.{username}.{name}`, so two Users of one browser never see each other's state and
 * signing out clears it. The theme stays global (`copycast.theme`, read before the app loads).
 *
 * Until a User is known (tests, or code that runs before `/api/me`), a name maps to its
 * pre-3.0 key `copycast.{name}`; the first User to sign in on a browser adopts those keys once.
 */

/** The names stored per User; their pre-3.0 keys are `copycast.{name}`. */
export const USER_STORAGE_NAMES = [
  "player",
  "player.track",
  "listening.v1",
  "catalog.columns",
  "buckets.open",
] as const;
export type UserStorageName = (typeof USER_STORAGE_NAMES)[number];

export const USER_STORAGE_PREFIX = "copycast.u.";
const LEGACY_PREFIX = "copycast.";

let currentUser: string | null = null;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The prefix of one User's keys. */
export function userPrefix(username: string): string {
  return `${USER_STORAGE_PREFIX}${username}.`;
}

/** The key `name` is stored under for the current User (the pre-3.0 key while nobody is). */
export function storageKey(name: UserStorageName): string {
  return currentUser === null ? `${LEGACY_PREFIX}${name}` : `${userPrefix(currentUser)}${name}`;
}

export function getStorageUser(): string | null {
  return currentUser;
}

/**
 * Switch the keys to `username` (null: nobody). The first User to sign in adopts the pre-3.0
 * keys: each moves under their prefix unless they already have that value, then it is deleted.
 * Returns whether the User changed.
 */
export function setStorageUser(username: string | null): boolean {
  if (username === currentUser) return false;
  currentUser = username;
  if (username !== null) adoptLegacyKeys(username);
  return true;
}

function adoptLegacyKeys(username: string): void {
  const store = storage();
  if (!store) return;
  try {
    for (const name of USER_STORAGE_NAMES) {
      const legacy = `${LEGACY_PREFIX}${name}`;
      const value = store.getItem(legacy);
      if (value === null) continue;
      const key = `${userPrefix(username)}${name}`;
      if (store.getItem(key) === null) store.setItem(key, value);
      store.removeItem(legacy);
    }
  } catch {
    // Storage may be full or unavailable; the User starts from defaults.
  }
}

/** Remove every key of `username` (sign-out). */
export function clearUserStorage(username: string): void {
  const store = storage();
  if (!store) return;
  try {
    const prefix = userPrefix(username);
    const keys: string[] = [];
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key?.startsWith(prefix)) keys.push(key);
    }
    for (const key of keys) store.removeItem(key);
  } catch {
    // Nothing more can be done without storage.
  }
}

/** Test seam: forget the current User. */
export function resetStorageUserForTests(): void {
  currentUser = null;
}
