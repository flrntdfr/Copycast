/**
 * The single global audio player. `PlayerBar` owns one `<audio preload="none">`;
 * this store is the intent (what to play) and the observed element state.
 */
import { useSyncExternalStore } from "react";

export interface PlayerTrack {
  itemId: string;
  feedId: string;
  title: string;
  feedTitle: string;
  url: string;
  mime: string;
  artworkUrl: string | null;
  durationSeconds: number | null;
}

export interface PlayerState {
  track: PlayerTrack | null;
  playing: boolean;
  currentTime: number;
  duration: number;
  error: string | null;
}

let state: PlayerState = { track: null, playing: false, currentTime: 0, duration: 0, error: null };
const listeners = new Set<() => void>();

function set(patch: Partial<PlayerState>): void {
  state = { ...state, ...patch };
  for (const listener of listeners) listener();
}

export function getPlayerState(): PlayerState {
  return state;
}

/** Play a track, or toggle when it is already the current one. */
export function play(track: PlayerTrack): void {
  if (state.track?.itemId === track.itemId) {
    set({ playing: !state.playing, error: null });
    return;
  }
  set({ track, playing: true, currentTime: 0, duration: track.durationSeconds ?? 0, error: null });
}

export function pause(): void {
  set({ playing: false });
}

export function resume(): void {
  if (state.track) set({ playing: true, error: null });
}

export function stop(): void {
  set({ track: null, playing: false, currentTime: 0, duration: 0, error: null });
}

export function seek(seconds: number): void {
  set({ currentTime: Math.max(0, seconds) });
}

/** Called by PlayerBar from the element's events. */
export function reportPlayback(
  patch: Partial<Pick<PlayerState, "playing" | "currentTime" | "duration" | "error">>,
): void {
  set(patch);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function usePlayer(): PlayerState {
  return useSyncExternalStore(subscribe, getPlayerState, getPlayerState);
}

export function useIsPlaying(itemId: string | null | undefined): boolean {
  const current = usePlayer();
  return !!itemId && current.track?.itemId === itemId && current.playing;
}
