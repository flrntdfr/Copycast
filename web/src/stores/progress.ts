/**
 * Live download progress, fed only by `progress` SSE frames and read through
 * `useSyncExternalStore`, keyed by job id and by item id. Never goes through React Query.
 */
import { useSyncExternalStore } from "react";

import type { JobProgress } from "@/api/types";

export interface ProgressEntry {
  jobId: string;
  feedId: string | null;
  itemId: string | null;
  progress: JobProgress;
  /** Wall-clock ms when the frame arrived; stale entries are swept. */
  at: number;
}

export const PROGRESS_TTL_MS = 60_000;

const byJob = new Map<string, ProgressEntry>();
const byItem = new Map<string, ProgressEntry>();
const listeners = new Set<() => void>();
let version = 0;

function emit(): void {
  version += 1;
  for (const listener of listeners) listener();
}

export function recordProgress(entry: Omit<ProgressEntry, "at"> & { at?: number }): void {
  const full: ProgressEntry = { ...entry, at: entry.at ?? Date.now() };
  byJob.set(full.jobId, full);
  if (full.itemId) byItem.set(full.itemId, full);
  emit();
}

/** Drop the entry once a job leaves the running state. */
export function clearProgress(jobId: string): void {
  const entry = byJob.get(jobId);
  if (!entry) return;
  byJob.delete(jobId);
  if (entry.itemId && byItem.get(entry.itemId)?.jobId === jobId) byItem.delete(entry.itemId);
  emit();
}

export function sweepProgress(now = Date.now(), ttl = PROGRESS_TTL_MS): number {
  let removed = 0;
  for (const [jobId, entry] of byJob) {
    if (now - entry.at > ttl) {
      byJob.delete(jobId);
      if (entry.itemId && byItem.get(entry.itemId)?.jobId === jobId) byItem.delete(entry.itemId);
      removed += 1;
    }
  }
  if (removed) emit();
  return removed;
}

export function resetProgress(): void {
  byJob.clear();
  byItem.clear();
  emit();
}

export function getJobProgress(jobId: string): ProgressEntry | undefined {
  return byJob.get(jobId);
}

export function getItemProgress(itemId: string): ProgressEntry | undefined {
  return byItem.get(itemId);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const getVersion = () => version;

export function useJobProgress(jobId: string | null | undefined): ProgressEntry | undefined {
  useSyncExternalStore(subscribe, getVersion, getVersion);
  return jobId ? byJob.get(jobId) : undefined;
}

export function useItemProgress(itemId: string | null | undefined): ProgressEntry | undefined {
  useSyncExternalStore(subscribe, getVersion, getVersion);
  return itemId ? byItem.get(itemId) : undefined;
}

/** Every live entry, newest first; for the Jobs screen. */
export function useAllProgress(): ProgressEntry[] {
  useSyncExternalStore(subscribe, getVersion, getVersion);
  return [...byJob.values()].sort((a, b) => b.at - a.at);
}
