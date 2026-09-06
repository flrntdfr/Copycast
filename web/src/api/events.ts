/**
 * One EventSource over `/api/events`. Frames carry `{"event": name, "data": {...}}`;
 * the six event names are used verbatim. Cache invalidation is debounced 250 ms per
 * query key; `progress` frames only feed the progress store. While the stream is
 * reconnecting the active queries are polled every 10 s and ConnectionDot turns amber.
 */
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

import { OPS } from "./ops";
import type { ArchiveState, JobProgress, JobRead, RequestStatus } from "./types";
import { clearProgress, recordProgress, sweepProgress } from "@/stores/progress";

export const EVENT_NAMES = ["job", "progress", "feed", "item", "request", "resync"] as const;
export type EventName = (typeof EVENT_NAMES)[number];

export interface JobEventData {
  job: JobRead;
}
export interface ProgressEventData {
  job_id: string;
  feed_id: string | null;
  item_id: string | null;
  progress: JobProgress;
}
export interface FeedEventData {
  feed_id: string;
  revision: number;
  reason: string;
}
export interface ItemEventData {
  feed_id: string;
  item_id: string;
  state: ArchiveState;
}
export interface RequestEventData {
  feed_id: string;
  request_id: string;
  status: RequestStatus;
  item_count: number;
}
export interface ResyncEventData {
  reason: string | null;
}

export type CopycastEvent =
  | { event: "job"; data: JobEventData }
  | { event: "progress"; data: ProgressEventData }
  | { event: "feed"; data: FeedEventData }
  | { event: "item"; data: ItemEventData }
  | { event: "request"; data: RequestEventData }
  | { event: "resync"; data: ResyncEventData };

/** Parse one SSE `data:` payload; null for anything that is not a Copycast event. */
export function parseEventPayload(raw: string): CopycastEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const { event, data } = parsed as { event?: unknown; data?: unknown };
  if (typeof event !== "string" || !(EVENT_NAMES as readonly string[]).includes(event)) return null;
  if (!data || typeof data !== "object") return null;
  return { event, data } as CopycastEvent;
}

export const INVALIDATE_DEBOUNCE_MS = 250;
export const RECONNECT_POLL_MS = 10_000;

/** Query keys (openapi-react-query shape) touched by each event. */
export function keysForEvent(event: CopycastEvent): QueryKey[] {
  const feedKey = (feedId: string): QueryKey => [
    OPS.get_feed[0],
    OPS.get_feed[1],
    { params: { path: { feed_id: feedId } } },
  ];
  const listFeeds: QueryKey = [OPS.list_feeds[0], OPS.list_feeds[1]];
  switch (event.event) {
    case "feed":
      return [listFeeds, feedKey(event.data.feed_id)];
    case "item":
      return [
        [
          OPS.list_items[0],
          OPS.list_items[1],
          { params: { path: { feed_id: event.data.feed_id } } },
        ],
        [
          OPS.get_item[0],
          OPS.get_item[1],
          { params: { path: { feed_id: event.data.feed_id, item_id: event.data.item_id } } },
        ],
      ];
    case "job": {
      const keys: QueryKey[] = [
        [OPS.list_jobs[0], OPS.list_jobs[1]],
        [OPS.get_job[0], OPS.get_job[1], { params: { path: { job_id: event.data.job.id } } }],
      ];
      if (event.data.job.feed_id) keys.push(listFeeds, feedKey(event.data.job.feed_id));
      return keys;
    }
    case "request":
      return [
        [
          OPS.list_requests[0],
          OPS.list_requests[1],
          { params: { path: { inbox_id: event.data.feed_id } } },
        ],
        [
          OPS.get_request[0],
          OPS.get_request[1],
          { params: { path: { inbox_id: event.data.feed_id, request_id: event.data.request_id } } },
        ],
        feedKey(event.data.feed_id),
      ];
    case "resync":
    case "progress":
      return [];
  }
}

/** Debounces `invalidateQueries` per serialized key. */
export class Invalidator {
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(
    private readonly queryClient: QueryClient,
    private readonly delay = INVALIDATE_DEBOUNCE_MS,
  ) {}

  schedule(queryKey: QueryKey): void {
    const id = JSON.stringify(queryKey);
    const existing = this.timers.get(id);
    if (existing) clearTimeout(existing);
    this.timers.set(
      id,
      setTimeout(() => {
        this.timers.delete(id);
        void this.queryClient.invalidateQueries({ queryKey });
      }, this.delay),
    );
  }

  /** Everything, now; used by `resync`. */
  all(): void {
    this.cancel();
    void this.queryClient.invalidateQueries();
  }

  cancel(): void {
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
  }

  get pending(): number {
    return this.timers.size;
  }
}

export type ConnectionStatus = "connecting" | "open" | "reconnecting";

const statusListeners = new Set<() => void>();
let connectionStatus: ConnectionStatus = "connecting";

function setStatus(next: ConnectionStatus): void {
  if (connectionStatus === next) return;
  connectionStatus = next;
  for (const listener of statusListeners) listener();
}

export function getConnectionStatus(): ConnectionStatus {
  return connectionStatus;
}

export function useConnectionStatus(): ConnectionStatus {
  return useSyncExternalStore(
    (listener) => {
      statusListeners.add(listener);
      return () => {
        statusListeners.delete(listener);
      };
    },
    getConnectionStatus,
    getConnectionStatus,
  );
}

/** Apply one event to the caches and stores; exported for tests. */
export function applyEvent(event: CopycastEvent, invalidator: Invalidator): void {
  switch (event.event) {
    case "progress":
      recordProgress({
        jobId: event.data.job_id,
        feedId: event.data.feed_id ?? null,
        itemId: event.data.item_id ?? null,
        progress: event.data.progress,
      });
      return;
    case "job":
      if (event.data.job.status !== "running") clearProgress(event.data.job.id);
      break;
    case "resync":
      invalidator.all();
      return;
    default:
      break;
  }
  for (const key of keysForEvent(event)) invalidator.schedule(key);
}

export type EventSourceLike = Pick<EventSource, "addEventListener" | "close"> & {
  onopen: ((this: EventSource, ev: Event) => unknown) | null;
  onerror: ((this: EventSource, ev: Event) => unknown) | null;
};

export interface EventStreamOptions {
  url?: string;
  createSource?: (url: string) => EventSourceLike;
  pollIntervalMs?: number;
  debounceMs?: number;
}

/**
 * Open the stream and keep the caches in sync until the returned function is called.
 * The browser resends `Last-Event-ID` on its own reconnects; the api replays or asks for a resync.
 */
export function startEventStream(
  queryClient: QueryClient,
  options: EventStreamOptions = {},
): () => void {
  const url = options.url ?? OPS.subscribe_events[1];
  const createSource = options.createSource ?? ((u: string) => new EventSource(u));
  const invalidator = new Invalidator(queryClient, options.debounceMs);
  let poll: ReturnType<typeof setInterval> | null = null;
  let sweep: ReturnType<typeof setInterval> | null = null;
  let closed = false;
  let everOpened = false;

  const stopPolling = () => {
    if (poll) clearInterval(poll);
    poll = null;
  };
  const startPolling = () => {
    if (poll || closed) return;
    poll = setInterval(() => {
      void queryClient.invalidateQueries({ refetchType: "active" });
    }, options.pollIntervalMs ?? RECONNECT_POLL_MS);
  };

  const source = createSource(url);
  source.onopen = () => {
    const reconnected = everOpened;
    everOpened = true;
    stopPolling();
    setStatus("open");
    // Anything that happened while the stream was down is fetched once.
    if (reconnected) invalidator.all();
  };
  source.onerror = () => {
    if (closed) return;
    setStatus("reconnecting");
    startPolling();
  };
  const onMessage = (message: MessageEvent<string>) => {
    const event = parseEventPayload(message.data);
    if (event) applyEvent(event, invalidator);
  };
  for (const name of EVENT_NAMES) source.addEventListener(name, onMessage as EventListener);
  sweep = setInterval(() => sweepProgress(), 30_000);
  setStatus("connecting");

  return () => {
    closed = true;
    stopPolling();
    if (sweep) clearInterval(sweep);
    invalidator.cancel();
    source.close();
    setStatus("connecting");
  };
}
