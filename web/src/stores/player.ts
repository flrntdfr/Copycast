/**
 * The single global audio player. `PlayerBar` owns one `<audio preload="none">`;
 * this store is the intent (what to play, how fast, how loud, when to stop) and the observed
 * element state. Speed, volume and the shortcut switch are remembered in localStorage, the
 * current track comes back (paused) after a reload, and positions go to the listening store.
 */
import { useSyncExternalStore } from "react";

import {
  FINISHED_TAIL_SECONDS,
  RESUME_MIN_SECONDS,
  readListening,
  resumeAt,
  writeListening,
} from "./listening";

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

export type SleepTimer =
  | { kind: "minutes"; minutes: number; deadline: number; leftMs: number }
  | { kind: "chapter"; endsAt: number }
  | { kind: "episode" };

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
  /** Single-key shortcuts (the player's keys, `/` and `?`); they can be turned off (WCAG 2.1.4). */
  shortcuts: boolean;
  sleep: SleepTimer | null;
}

export const RATE_MIN = 0.5;
export const RATE_MAX = 3;
export const RATE_STEP = 0.05;
/** What `[` and `]` change the speed by. */
export const RATE_KEY_STEP = 0.1;
export const RATE_PRESETS = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3] as const;
export const SKIP_BACK_SECONDS = 15;
export const SKIP_FORWARD_SECONDS = 30;
/** Previous chapter restarts the current one when more than this far into it. */
export const CHAPTER_RESTART_SECONDS = 3;
export const SLEEP_MINUTES = [5, 15, 30, 45, 60] as const;
/** A minutes sleep timer fades the volume out over its last 10 s. */
export const SLEEP_FADE_MS = 10_000;
/** Positions are saved at most this often from playback, and at once on pause, seek and hide. */
export const LISTENING_SAVE_INTERVAL_MS = 5_000;
export const PLAYER_STORAGE_KEY = "copycast.player";
export const PLAYER_TRACK_STORAGE_KEY = "copycast.player.track";

/** A sleep timer choice: minutes, the end of the current chapter or Episode, or off. */
export type SleepChoice = (typeof SLEEP_MINUTES)[number] | "chapter" | "episode" | null;

/** A speed on the 0.05 grid within 0.5–3×. */
export function clampRate(rate: number): number {
  if (!Number.isFinite(rate)) return 1;
  const clamped = Math.min(RATE_MAX, Math.max(RATE_MIN, rate));
  return Number((Math.round(clamped / RATE_STEP) * RATE_STEP).toFixed(2));
}

/** "1.35", "1.5", "2": the speed without trailing zeros. */
export function formatRate(rate: number): string {
  return String(Number(rate.toFixed(2)));
}

interface StoredPrefs {
  rate?: number;
  volume?: number;
  muted?: boolean;
  shortcuts?: boolean;
}

function readPrefs(): Required<StoredPrefs> {
  const fallback = { rate: 1, volume: 1, muted: false, shortcuts: true };
  try {
    const raw = window.localStorage.getItem(PLAYER_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as StoredPrefs;
    const rate =
      typeof parsed.rate === "number" && parsed.rate >= RATE_MIN && parsed.rate <= RATE_MAX
        ? clampRate(parsed.rate)
        : 1;
    const volume =
      typeof parsed.volume === "number" && parsed.volume >= 0 && parsed.volume <= 1
        ? parsed.volume
        : 1;
    return { rate, volume, muted: parsed.muted === true, shortcuts: parsed.shortcuts !== false };
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

/** The track to bring back after a reload (media URLs carry no credentials). */
function readTrackSnapshot(): PlayerTrack | null {
  try {
    const raw = window.localStorage.getItem(PLAYER_TRACK_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<PlayerTrack> | null;
    if (!value || typeof value !== "object") return null;
    const { itemId, feedId, title, feedTitle, url, mime, artworkUrl, durationSeconds } = value;
    if (
      typeof itemId !== "string" ||
      typeof feedId !== "string" ||
      typeof title !== "string" ||
      typeof feedTitle !== "string" ||
      typeof url !== "string" ||
      typeof mime !== "string" ||
      !itemId ||
      !url
    )
      return null;
    return {
      itemId,
      feedId,
      title,
      feedTitle,
      url,
      mime,
      artworkUrl: typeof artworkUrl === "string" ? artworkUrl : null,
      durationSeconds:
        typeof durationSeconds === "number" && Number.isFinite(durationSeconds)
          ? durationSeconds
          : null,
      chaptersUrl: typeof value.chaptersUrl === "string" ? value.chaptersUrl : null,
    };
  } catch {
    return null;
  }
}

function writeTrackSnapshot(track: PlayerTrack | null): void {
  try {
    if (track) window.localStorage.setItem(PLAYER_TRACK_STORAGE_KEY, JSON.stringify(track));
    else window.localStorage.removeItem(PLAYER_TRACK_STORAGE_KEY);
  } catch {
    // Storage may be unavailable; the bar just does not come back after a reload.
  }
}

function initialState(): PlayerState {
  const prefs = readPrefs();
  const idle: PlayerState = {
    track: null,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: null,
    rate: prefs.rate,
    volume: prefs.volume,
    muted: prefs.muted,
    chapters: [],
    shortcuts: prefs.shortcuts,
    sleep: null,
  };
  // The last track comes back paused: autoplay rules forbid play() without a gesture.
  const track = readTrackSnapshot();
  if (!track) return idle;
  const entry = readListening(track.itemId);
  const currentTime = resumeAt(entry, Date.now());
  restored = { itemId: track.itemId, t: currentTime };
  return {
    ...idle,
    track,
    currentTime,
    duration: entry?.d ?? track.durationSeconds ?? 0,
  };
}

/**
 * The resume point a track starts from (rewound a little after a break). Until the position
 * moves away from it nothing was heard, and saving it would rewind the stored position again
 * on every reload.
 */
let restored: { itemId: string; t: number } | null = null;
let state: PlayerState = initialState();
let lastSavedAt = 0;
const listeners = new Set<() => void>();

function set(patch: Partial<PlayerState>): void {
  state = { ...state, ...patch };
  for (const listener of listeners) listener();
}

function persist(): void {
  writePrefs({
    rate: state.rate,
    volume: state.volume,
    muted: state.muted,
    shortcuts: state.shortcuts,
  });
}

function knownDuration(): number | null {
  if (Number.isFinite(state.duration) && state.duration > 0) return state.duration;
  const fallback = state.track?.durationSeconds;
  return fallback && fallback > 0 ? fallback : null;
}

export function getPlayerState(): PlayerState {
  return state;
}

/**
 * Remember the current position. `ending` (stop, switching track) also marks the Episode
 * Finished when under 30 s of a known duration remain; a Finished Episode played again
 * stays Finished until it passes 10 s.
 */
export function saveListening({ ending = false }: { ending?: boolean } = {}): void {
  const { track, currentTime } = state;
  if (!track) return;
  if (restored?.itemId === track.itemId && restored.t === currentTime) return;
  restored = null;
  lastSavedAt = Date.now();
  const d = knownDuration();
  const previous = readListening(track.itemId);
  const finished =
    (ending && d !== null && d - currentTime < FINISHED_TAIL_SECONDS) ||
    (previous?.finished === true && currentTime <= RESUME_MIN_SECONDS);
  writeListening(track.itemId, { t: currentTime, d, at: lastSavedAt, finished });
}

/** The element played to the end: the Episode is Finished. */
export function finishTrack(): void {
  const { track, currentTime, sleep } = state;
  if (!track) return;
  lastSavedAt = Date.now();
  writeListening(track.itemId, {
    t: currentTime,
    d: knownDuration(),
    at: lastSavedAt,
    finished: true,
  });
  set({ playing: false, sleep: sleep?.kind === "minutes" ? sleep : null });
}

/** Play a track from where it was left, or toggle when it is already the current one. */
export function play(track: PlayerTrack): void {
  if (state.track?.itemId === track.itemId) {
    set({ playing: !state.playing, error: null });
    return;
  }
  if (state.track) saveListening({ ending: true });
  const entry = readListening(track.itemId);
  const currentTime = resumeAt(entry, Date.now());
  restored = { itemId: track.itemId, t: currentTime };
  writeTrackSnapshot(track);
  set({
    track,
    playing: true,
    currentTime,
    duration: entry?.d ?? track.durationSeconds ?? 0,
    error: null,
    chapters: [],
    // A timer tied to the old Episode's chapter or end does not carry over; minutes keep counting.
    sleep: state.sleep?.kind === "minutes" ? state.sleep : null,
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
  saveListening({ ending: true });
  writeTrackSnapshot(null);
  set({
    track: null,
    playing: false,
    currentTime: 0,
    duration: 0,
    error: null,
    chapters: [],
    sleep: null,
  });
}

/** The media failed: report it, and do not bring this track back after a reload. */
export function failPlayback(error: string): void {
  writeTrackSnapshot(null);
  set({ playing: false, error });
}

export function seek(seconds: number): void {
  const limit = state.duration > 0 ? state.duration : Number.POSITIVE_INFINITY;
  set({ currentTime: Math.min(Math.max(0, seconds), limit) });
}

export function skip(deltaSeconds: number): void {
  seek(state.currentTime + deltaSeconds);
}

export function setRate(rate: number): void {
  set({ rate: clampRate(rate) });
  persist();
}

/** Faster or slower by `delta`, clamped to 0.5–3× (no wrap-around). */
export function stepRate(delta: number): void {
  setRate(state.rate + delta);
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

export function setShortcuts(enabled: boolean): void {
  set({ shortcuts: enabled });
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

/**
 * Where previous (-1) or next (1) chapter goes from `position`, or null when there is
 * nowhere to go. Previous restarts the current chapter when more than 3 s into it.
 */
export function chapterTarget(
  chapters: Chapter[],
  position: number,
  direction: 1 | -1,
): number | null {
  if (chapters.length === 0) return null;
  const index = chapters.findLastIndex((chapter) => chapter.startTime <= position);
  if (direction === 1) return chapters[index + 1]?.startTime ?? null;
  const current = chapters[index];
  if (!current) return 0;
  if (position - current.startTime > CHAPTER_RESTART_SECONDS) return current.startTime;
  return chapters[index - 1]?.startTime ?? current.startTime;
}

/** Seek to the previous or next chapter; false when there is none. */
export function skipChapter(direction: 1 | -1): boolean {
  const target = chapterTarget(state.chapters, state.currentTime, direction);
  if (target === null) return false;
  seek(target);
  return true;
}

/**
 * Start or cancel the sleep timer. Minutes run on the wall clock from now; the end of
 * chapter is the next chapter's start (the end of the Episode after the last one).
 */
export function setSleepTimer(choice: SleepChoice, nowMs = Date.now()): void {
  if (choice === null) {
    set({ sleep: null });
  } else if (choice === "episode") {
    set({ sleep: { kind: "episode" } });
  } else if (choice === "chapter") {
    const next = state.chapters.find((chapter) => chapter.startTime > state.currentTime);
    set({ sleep: { kind: "chapter", endsAt: next?.startTime ?? Number.POSITIVE_INFINITY } });
  } else {
    const leftMs = choice * 60_000;
    set({ sleep: { kind: "minutes", minutes: choice, deadline: nowMs + leftMs, leftMs } });
  }
}

/**
 * Advance the sleep timer: when it runs out it pauses and clears. Returns the factor (0–1)
 * for the element volume, below 1 during the fade of a minutes timer and 0 once it has run
 * out (the element stays silent until it has paused). Pausing by hand does
 * not cancel a minutes timer: the deadline keeps counting and, if it passes while paused,
 * the timer simply ends. The end of the Episode clears in `finishTrack`.
 */
export function tickSleepTimer(nowMs: number): number {
  const { sleep } = state;
  if (!sleep) return 1;
  if (sleep.kind === "minutes") {
    const leftMs = Math.max(0, sleep.deadline - nowMs);
    if (leftMs === 0) {
      set({ sleep: null, playing: false });
      return 0;
    }
    if (Math.ceil(leftMs / 1000) !== Math.ceil(sleep.leftMs / 1000))
      set({ sleep: { ...sleep, leftMs } });
    return leftMs < SLEEP_FADE_MS ? leftMs / SLEEP_FADE_MS : 1;
  }
  if (sleep.kind === "chapter" && state.currentTime >= sleep.endsAt) {
    set({ sleep: null, playing: false });
    return 0;
  }
  return 1;
}

/** Called by PlayerBar from the element's events; saves the position every few seconds. */
export function reportPlayback(
  patch: Partial<Pick<PlayerState, "playing" | "currentTime" | "duration" | "error">>,
): void {
  set(patch);
  if (
    patch.currentTime !== undefined &&
    state.track &&
    Date.now() - lastSavedAt >= LISTENING_SAVE_INTERVAL_MS
  )
    saveListening();
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

/**
 * One slice of the player state. The component re-renders only when the slice changes,
 * not on every `timeupdate`, so return a primitive.
 */
export function usePlayerSelector<T>(selector: (state: PlayerState) => T): T {
  const snapshot = () => selector(state);
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function useIsPlaying(itemId: string | null | undefined): boolean {
  return usePlayerSelector(
    (current) => !!itemId && current.track?.itemId === itemId && current.playing,
  );
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
    shortcuts: true,
    sleep: null,
  };
  restored = null;
  lastSavedAt = 0;
  for (const listener of listeners) listener();
}

/** Test seam: read the preferences and the last track back from storage, as a reload does. */
export function reloadPlayerForTests(): void {
  state = initialState();
  lastSavedAt = 0;
  for (const listener of listeners) listener();
}
