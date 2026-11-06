/**
 * What changes with the signed-in User: whose localStorage keys the stores read, the one event
 * stream, and what signing out (or being signed out) clears. Kept outside React so the 401
 * handler, the layout's `beforeLoad` and the user menu share one implementation.
 */
import type { QueryClient } from "@tanstack/react-query";

import { startEventStream, type EventStreamOptions } from "@/api/events";
import { clearUserStorage, getStorageUser, setStorageUser } from "@/lib/storage";
import { reloadPlayer, resetPlayer, stop as stopPlayback } from "@/stores/player";

/**
 * Point the stores at `username`'s keys. When the User changed, the player re-reads its
 * preferences and last track (theirs, not the previous User's or the anonymous ones).
 */
export function applySessionUser(username: string): void {
  if (setStorageUser(username)) reloadPlayer();
}

let stopStream: (() => void) | null = null;
let streamUser: string | null = null;

/** Start the event stream for `username`, restarting it when the User changed. */
export function startSessionEvents(
  queryClient: QueryClient,
  username: string,
  options?: EventStreamOptions,
): () => void {
  if (stopStream && streamUser === username) return stopSessionEvents;
  stopSessionEvents();
  stopStream = startEventStream(queryClient, options);
  streamUser = username;
  return stopSessionEvents;
}

export function stopSessionEvents(): void {
  stopStream?.();
  stopStream = null;
  streamUser = null;
}

/**
 * Forget everything the signed-in User left in this tab: playback stops, the event stream
 * closes, their namespaced keys are deleted and every cached response is dropped.
 */
export function clearSession(queryClient: QueryClient): void {
  const username = getStorageUser();
  stopPlayback();
  stopSessionEvents();
  if (username !== null) clearUserStorage(username);
  setStorageUser(null);
  resetPlayer();
  queryClient.clear();
}

/**
 * A 401 from `/api`: the session ended elsewhere (expired, signed out in another tab, the
 * User removed). Playback stops and the caches go, but the User's keys stay: signing back in
 * finds their positions where they were.
 */
export function endSession(queryClient: QueryClient): void {
  stopPlayback();
  stopSessionEvents();
  queryClient.clear();
}
