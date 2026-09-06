/** HealthDot colour and wording derived from `MirrorRead.health` and the refresh timestamps. */
import type { HealthStatus, MirrorRead } from "@/api/types";
import { formatRelative } from "@/lib/format";

export type HealthTone = "success" | "warning" | "destructive" | "muted";

export interface HealthView {
  status: HealthStatus;
  tone: HealthTone;
  /** Short wording next to the dot, e.g. "Refreshed 3 hours ago". */
  text: string;
  /** Longer wording for tooltips; includes the reason when the API gives one. */
  detail: string;
}

type HealthInput = Pick<
  MirrorRead,
  "health" | "paused" | "last_refresh_attempt_at" | "last_refresh_success_at" | "last_error"
>;

export function healthTone(status: string): HealthTone {
  switch (status) {
    case "ok":
      return "success";
    case "warn":
      return "warning";
    case "error":
      return "destructive";
    default:
      return "muted";
  }
}

export function describeHealth(mirror: HealthInput, now = new Date()): HealthView {
  const status = mirror.health.status;
  const reason = mirror.health.reason?.trim() || null;
  const tone = healthTone(status);
  const lastSuccess = mirror.last_refresh_success_at;
  const lastAttempt = mirror.last_refresh_attempt_at;
  const relative = (value: string | null | undefined) => formatRelative(value, now);

  if (mirror.paused || status === "paused") {
    const text = "Paused";
    const detail = lastSuccess
      ? `Paused; last Refresh ${relative(lastSuccess)}`
      : "Paused; never refreshed";
    return { status, tone, text, detail: reason ? `${detail}. ${reason}` : detail };
  }
  if (status === "never" || (!lastAttempt && !lastSuccess)) {
    return {
      status,
      tone,
      text: "Never refreshed",
      detail: reason ?? "Waiting for the first Refresh",
    };
  }
  if (status === "error") {
    const text = lastSuccess ? `Failing since ${relative(lastSuccess)}` : "Failing";
    const detail = reason ?? mirror.last_error ?? "The last Refresh failed";
    return { status, tone, text, detail };
  }
  if (status === "warn") {
    const text = lastSuccess ? `Warnings; refreshed ${relative(lastSuccess)}` : "Warnings";
    const detail = reason ?? mirror.last_error ?? "The last Refresh reported warnings";
    return { status, tone, text, detail };
  }
  const text = lastSuccess
    ? `Refreshed ${relative(lastSuccess)}`
    : `Attempted ${relative(lastAttempt)}`;
  return { status, tone, text, detail: reason ?? text };
}

export const HEALTH_DOT_CLASS: Record<HealthTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  muted: "bg-muted-foreground/50",
};
