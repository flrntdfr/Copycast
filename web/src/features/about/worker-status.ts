/**
 * "Worker last seen" for the About page, read from `/healthz/ready`. The route answers
 * 503 with the same body when degraded, so the body is parsed whatever the status.
 */
import { useQuery } from "@tanstack/react-query";

import { API_BASE_URL } from "@/api/client";
import { EXTRA_OPS } from "@/api/ops";
import type { ReadyRead } from "@/api/types";
import { formatRelative, parseDate } from "@/lib/format";

export const WORKER_STATUS_POLL_MS = 30_000;

export type WorkerSeen =
  | { kind: "seen"; at: string; stale: boolean }
  | { kind: "never" }
  | { kind: "unavailable"; detail: string | null }
  | { kind: "unknown" };

/** Interpret the `worker_seen_at` readiness check (ISO timestamp, "never" or an error). */
export function workerSeenOf(ready: ReadyRead | undefined): WorkerSeen {
  const check = ready?.checks.worker_seen_at;
  if (!check) return { kind: "unknown" };
  if (check.detail && parseDate(check.detail)) {
    return { kind: "seen", at: check.detail, stale: !check.ok };
  }
  if (check.detail === "never") return { kind: "never" };
  return { kind: "unavailable", detail: check.detail ?? null };
}

export function describeWorkerSeen(seen: WorkerSeen, now = new Date()): string {
  switch (seen.kind) {
    case "seen":
      return seen.stale
        ? `${formatRelative(seen.at, now)} (not running?)`
        : formatRelative(seen.at, now);
    case "never":
      return "never";
    case "unavailable":
      return seen.detail ? `unknown (${seen.detail})` : "unknown";
    case "unknown":
      return "unknown";
  }
}

export async function fetchReady(signal?: AbortSignal): Promise<ReadyRead> {
  const response = await fetch(new URL(EXTRA_OPS.health_ready[1], API_BASE_URL || undefined), {
    headers: { Accept: "application/json" },
    signal,
  });
  const body: unknown = await response.json();
  if (!body || typeof body !== "object" || !("checks" in body)) {
    throw new Error(`Unexpected readiness document (HTTP ${response.status})`);
  }
  return body as ReadyRead;
}

export function useWorkerStatus() {
  return useQuery({
    queryKey: [EXTRA_OPS.health_ready[0], EXTRA_OPS.health_ready[1]],
    queryFn: ({ signal }) => fetchReady(signal),
    refetchInterval: WORKER_STATUS_POLL_MS,
    retry: false,
  });
}
