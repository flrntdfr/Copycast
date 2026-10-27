/**
 * Where you are in each Episode, remembered per browser (the server copy comes later).
 * An LRU of the most recently heard Episodes in localStorage, keyed by item id; reads and
 * writes tolerate missing or corrupt JSON and a storage that throws.
 */

export const LISTENING_STORAGE_KEY = "copycast.listening.v1";
export const LISTENING_MAX_ENTRIES = 500;
/** Under this many seconds in, an Episode starts over. */
export const RESUME_MIN_SECONDS = 10;
/** Within this many seconds of the end, an Episode starts over (and counts as Finished on stop). */
export const FINISHED_TAIL_SECONDS = 30;

export interface ListeningEntry {
  /** Position in seconds. */
  t: number;
  /** Duration in seconds, when known. */
  d: number | null;
  /** When the position was saved, epoch ms. */
  at: number;
  finished: boolean;
}

type ListeningMap = Record<string, ListeningEntry>;

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

function isEntry(value: unknown): value is ListeningEntry {
  if (!value || typeof value !== "object") return false;
  const { t, d, at, finished } = value as Partial<ListeningEntry>;
  return (
    typeof t === "number" &&
    Number.isFinite(t) &&
    t >= 0 &&
    (d === null || (typeof d === "number" && Number.isFinite(d) && d > 0)) &&
    typeof at === "number" &&
    Number.isFinite(at) &&
    typeof finished === "boolean"
  );
}

function readAll(): ListeningMap {
  try {
    const raw = window.localStorage.getItem(LISTENING_STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const entries: ListeningMap = {};
    for (const [itemId, entry] of Object.entries(parsed as Record<string, unknown>)) {
      if (isEntry(entry)) entries[itemId] = entry;
    }
    return entries;
  } catch {
    return {};
  }
}

export function readListening(itemId: string): ListeningEntry | null {
  return readAll()[itemId] ?? null;
}

/** Save one Episode's entry; the least recently saved ones go beyond the cap. */
export function writeListening(itemId: string, entry: ListeningEntry): void {
  const entries = readAll();
  entries[itemId] = entry;
  let kept = entries;
  const ids = Object.keys(entries);
  if (ids.length > LISTENING_MAX_ENTRIES) {
    ids.sort((a, b) => (entries[b]?.at ?? 0) - (entries[a]?.at ?? 0));
    kept = {};
    for (const id of ids.slice(0, LISTENING_MAX_ENTRIES)) {
      const value = entries[id];
      if (value) kept[id] = value;
    }
  }
  try {
    window.localStorage.setItem(LISTENING_STORAGE_KEY, JSON.stringify(kept));
  } catch {
    // Storage may be full or unavailable; playback still works.
  }
}

/**
 * Where to start an Episode: 0 when there is no entry, it is Finished, it barely started
 * or it is nearly over; otherwise the saved position minus an auto-rewind that grows with
 * the time away (3 s after a minute, 10 s after an hour, 20 s after a day).
 */
export function resumeAt(entry: ListeningEntry | null, nowMs: number): number {
  if (!entry || entry.finished || entry.t < RESUME_MIN_SECONDS) return 0;
  if (entry.d !== null && entry.d - entry.t < FINISHED_TAIL_SECONDS) return 0;
  const idle = nowMs - entry.at;
  const rewind = idle > DAY_MS ? 20 : idle > HOUR_MS ? 10 : idle > MINUTE_MS ? 3 : 0;
  return Math.max(0, entry.t - rewind);
}
