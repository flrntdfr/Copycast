/**
 * The single global audio player. `PlayerBar` owns one `<audio preload="none">`;
 * this store is the intent (what to play, how fast, how loud) and the observed
 * element state. Speed and volume are remembered in localStorage.
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
  /** The Episode's chapters asset (Podcasting 2.0 JSON), shown as marks on the seek bar. */
  chaptersUrl?: string | null;
}

export interface Chapter {
  startTime: number;
  title: string;
}

export interface PlayerState {
  track: PlayerTrack | null;
  playing: boolean;
  currentTime: number;
  duration: number;
  error: string | null;
  rate: number;
  volume: number;
  muted: boolean;
  chapters: Chapter[];
}

export const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2] as const;
export const SKIP_BACK_SECONDS = 15;
export const SKIP_FORWARD_SECONDS = 30;
export const PLAYER_STORAGE_KEY = "copycast.player";

interface StoredPrefs {
  rate?: number;
  volume?: number;
  muted?: boolean;
}

function readPrefs(): Required<StoredPrefs> {
  const fallback = { rate: 1, volume: 1, muted: false };
  try {
    const raw = window.localStorage.getItem(PLAYER_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as StoredPrefs;
    const rate =
      typeof parsed.rate === "number" && RATES.includes(parsed.rate as never) ? parsed.rate : 1;
    const volume =
      typeof parsed.volume === "number" && parsed.volume >= 0 && parsed.volume <= 1
        ? parsed.volume
        : 1;
    return { rate, volume, muted: parsed.muted === true };
  } catch {
    return fallback;
  }
}

function writePrefs(prefs: StoredPrefs): void {
  try {
    window.localStorage.setItem(PLAYER_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage may be unavailable; the session still works.
  }
}

const prefs = readPrefs();
let state: PlayerState = {
  track: null,
  playing: false,
  currentTime: 0,
  duration: 0,
  error: null,
  rate: prefs.rate,
  volume: prefs.volume,
  muted: prefs.muted,
  chapters: [],
};
const listeners = new Set<() => void>();

function set(patch: Partial<PlayerState>): void {
  state = { ...state, ...patch };
  for (const listener of listeners) listener();
}

function persist(): void {
  writePrefs({ rate: state.rate, volume: state.volume, muted: state.muted });
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
  set({
    track,
    playing: true,
    currentTime: 0,
    duration: track.durationSeconds ?? 0,
    error: null,
    chapters: [],
  });
}

export function pause(): void {
  set({ playing: false });
}

export function resume(): void {
  if (state.track) set({ playing: true, error: null });
}

export function togglePlay(): void {
  if (!state.track) return;
  if (state.playing) pause();
  else resume();
}

export function stop(): void {
  set({ track: null, playing: false, currentTime: 0, duration: 0, error: null, chapters: [] });
}

export function seek(seconds: number): void {
  const limit = state.duration > 0 ? state.duration : Number.POSITIVE_INFINITY;
  set({ currentTime: Math.min(Math.max(0, seconds), limit) });
}

export function skip(deltaSeconds: number): void {
  seek(state.currentTime + deltaSeconds);
}

export function setRate(rate: number): void {
  set({ rate });
  persist();
}

/** The next speed after the current one, wrapping around. */
export function cycleRate(direction: 1 | -1 = 1): void {
  const index = RATES.indexOf(state.rate as (typeof RATES)[number]);
  const next = RATES[(index + direction + RATES.length) % RATES.length] ?? 1;
  setRate(next);
}

export function setVolume(volume: number): void {
  const clamped = Math.min(1, Math.max(0, volume));
  set({ volume: clamped, muted: clamped === 0 ? state.muted : false });
  persist();
}

export function toggleMute(): void {
  set({ muted: !state.muted });
  persist();
}

export function setChapters(chapters: Chapter[]): void {
  set({ chapters });
}

/** The chapter playing at `seconds`, if any. */
export function chapterAt(chapters: Chapter[], seconds: number): Chapter | null {
  let current: Chapter | null = null;
  for (const chapter of chapters) {
    if (chapter.startTime <= seconds) current = chapter;
    else break;
  }
  return current;
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

/** Test seam: forget the track and restore the built-in preferences. */
export function resetPlayerForTests(): void {
  state = {
    track: null,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: null,
    rate: 1,
    volume: 1,
    muted: false,
    chapters: [],
  };
  for (const listener of listeners) listener();
}
