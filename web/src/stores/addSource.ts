/**
 * The Add Source dialog's intent: opened with a URL to probe or a name to search for,
 * from the Mirrors page bar, the command palette, or the Inboxes page's capture card.
 */
import { useSyncExternalStore } from "react";

export interface AddSourceRequest {
  /** A Source URL (or an Apple Podcasts / Spotify link) to probe. */
  url?: string;
  /** Free text to search Apple's directory and YouTube for. */
  query?: string;
  /** Create the Mirror in sync with the Source (a private playlist captured from YouTube). */
  sync?: boolean;
}

export interface AddSourceState extends AddSourceRequest {
  open: boolean;
  /** Bumped on every open so the dialog starts over even for the same URL. */
  nonce: number;
}

let state: AddSourceState = { open: false, nonce: 0 };
const listeners = new Set<() => void>();

function set(next: AddSourceState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function openAddSource(request: AddSourceRequest = {}): void {
  set({
    open: true,
    nonce: state.nonce + 1,
    url: request.url?.trim() || undefined,
    query: request.query?.trim() || undefined,
    sync: request.sync,
  });
}

export function closeAddSource(): void {
  if (state.open) set({ ...state, open: false });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function snapshot(): AddSourceState {
  return state;
}

export function useAddSource(): AddSourceState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** "https://…", "example.com/feed", "open.spotify.com/show/…": a URL rather than a name. */
export function looksLikeSourceUrl(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || /\s/.test(trimmed)) return false;
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) || /^[\w.-]+\.[a-z]{2,}(\/|$)/i.test(trimmed);
}
