/** Formatting helpers; every function tolerates null so table cells never throw. */

const UNITS = ["B", "kB", "MB", "GB", "TB"] as const;

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes)) return "";
  if (bytes < 1000) return `${Math.round(bytes)} B`;
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : digits)} ${UNITS[unit]}`;
}

export function formatSpeed(bps: number | null | undefined): string {
  if (bps == null || !Number.isFinite(bps)) return "";
  return `${formatBytes(bps)}/s`;
}

/** `h:mm:ss` above one hour, `m:ss` otherwise. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** Human ETA such as "3 min" or "1 h 12 min". */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "";
  return `${Math.max(0, Math.min(100, value)).toFixed(0)}%`;
}

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatDate(value: string | null | undefined): string {
  const date = parseDate(value);
  return date ? dateFormatter.format(date) : "";
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parseDate(value);
  return date ? dateTimeFormatter.format(date) : "";
}

const relativeFormatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

const STEPS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "3 hours ago" / "in 2 days"; "just now" under a minute. */
export function formatRelative(value: string | Date | null | undefined, now = new Date()): string {
  const date = value instanceof Date ? value : parseDate(value);
  if (!date) return "";
  const diff = (date.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return "just now";
  for (const [unit, size] of STEPS) {
    if (abs >= size) return relativeFormatter.format(Math.round(diff / size), unit);
  }
  return "just now";
}

/** Milliseconds between two ISO timestamps, or from `start` to now. */
export function elapsedMs(start: string | null | undefined, end?: string | null): number | null {
  const a = parseDate(start);
  if (!a) return null;
  const b = end ? parseDate(end) : new Date();
  if (!b) return null;
  return Math.max(0, b.getTime() - a.getTime());
}

export function formatElapsed(ms: number | null | undefined): string {
  if (ms == null) return "";
  return formatDuration(ms / 1000);
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "";
  return value.toLocaleString();
}
