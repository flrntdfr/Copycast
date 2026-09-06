import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  Invalidator,
  applyEvent,
  getConnectionStatus,
  keysForEvent,
  parseEventPayload,
  startEventStream,
  type CopycastEvent,
  type EventSourceLike,
} from "./events";
import { OPS } from "./ops";
import { job } from "@/test/factories";
import { getItemProgress, getJobProgress, resetProgress } from "@/stores/progress";

describe("parseEventPayload", () => {
  it("accepts the six event names verbatim and rejects the rest", () => {
    expect(
      parseEventPayload('{"event":"feed","data":{"feed_id":"m1","revision":2,"reason":"refresh"}}'),
    ).toEqual({
      event: "feed",
      data: { feed_id: "m1", revision: 2, reason: "refresh" },
    });
    expect(parseEventPayload('{"event":"export","data":{}}')).toBeNull();
    expect(parseEventPayload("not json")).toBeNull();
    expect(parseEventPayload('{"event":"feed"}')).toBeNull();
  });
});

describe("keysForEvent", () => {
  it("maps feed events to list_feeds and get_feed", () => {
    const keys = keysForEvent({ event: "feed", data: { feed_id: "m1", revision: 1, reason: "x" } });
    expect(keys).toEqual([
      [OPS.list_feeds[0], OPS.list_feeds[1]],
      [OPS.get_feed[0], OPS.get_feed[1], { params: { path: { feed_id: "m1" } } }],
    ]);
  });

  it("maps item events to that feed's list_items", () => {
    const keys = keysForEvent({
      event: "item",
      data: { feed_id: "m1", item_id: "i1", state: "archived" },
    });
    expect(keys[0]).toEqual([
      OPS.list_items[0],
      OPS.list_items[1],
      { params: { path: { feed_id: "m1" } } },
    ]);
  });

  it("maps job events to list_jobs plus the feed", () => {
    const keys = keysForEvent({ event: "job", data: { job: job({ id: "j1", feed_id: "m1" }) } });
    expect(keys.map((key) => key[1])).toEqual([
      OPS.list_jobs[1],
      OPS.get_job[1],
      OPS.list_feeds[1],
      OPS.get_feed[1],
    ]);
  });

  it("maps request events to list_requests", () => {
    const keys = keysForEvent({
      event: "request",
      data: { feed_id: "in1", request_id: "r1", status: "expanded", item_count: 3 },
    });
    expect(keys[0]).toEqual([
      OPS.list_requests[0],
      OPS.list_requests[1],
      { params: { path: { inbox_id: "in1" } } },
    ]);
  });

  it("progress and resync touch no query key", () => {
    expect(keysForEvent({ event: "resync", data: { reason: null } })).toEqual([]);
  });
});

describe("Invalidator", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("debounces per serialized key for 250 ms", () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const invalidator = new Invalidator(client);
    const key = [OPS.list_feeds[0], OPS.list_feeds[1]];
    invalidator.schedule(key);
    invalidator.schedule([...key]);
    invalidator.schedule([OPS.list_jobs[0], OPS.list_jobs[1]]);
    expect(invalidator.pending).toBe(2);
    vi.advanceTimersByTime(249);
    expect(spy).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy).toHaveBeenCalledWith({ queryKey: key });
    expect(invalidator.pending).toBe(0);
  });

  it("resync invalidates everything immediately and drops pending timers", () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const invalidator = new Invalidator(client);
    invalidator.schedule(["get", "/api/feeds"]);
    applyEvent({ event: "resync", data: { reason: "rebuild" } }, invalidator);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith();
    expect(invalidator.pending).toBe(0);
  });
});

describe("applyEvent", () => {
  beforeEach(() => resetProgress());

  it("routes progress frames into the progress store only", () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const invalidator = new Invalidator(client, 0);
    applyEvent(
      {
        event: "progress",
        data: {
          job_id: "j1",
          feed_id: "m1",
          item_id: "i1",
          progress: { phase: "downloading", percent: 42 },
        },
      },
      invalidator,
    );
    expect(getJobProgress("j1")?.progress.percent).toBe(42);
    expect(getItemProgress("i1")?.jobId).toBe("j1");
    expect(invalidator.pending).toBe(0);
    expect(spy).not.toHaveBeenCalled();
  });

  it("clears progress once the job leaves running", () => {
    const invalidator = new Invalidator(new QueryClient(), 0);
    applyEvent(
      {
        event: "progress",
        data: {
          job_id: "j1",
          feed_id: "m1",
          item_id: "i1",
          progress: { phase: "downloading", percent: 90 },
        },
      },
      invalidator,
    );
    applyEvent(
      { event: "job", data: { job: job({ id: "j1", status: "succeeded" }) } },
      invalidator,
    );
    expect(getJobProgress("j1")).toBeUndefined();
    expect(getItemProgress("i1")).toBeUndefined();
    invalidator.cancel();
  });
});

class FakeSource {
  static instances: FakeSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent<string>) => unknown>();
  closed = false;
  constructor(public url: string) {
    FakeSource.instances.push(this);
  }
  addEventListener(name: string, listener: (event: MessageEvent<string>) => unknown): void {
    this.listeners.set(name, listener);
  }
  close(): void {
    this.closed = true;
  }
  emit(name: string, event: CopycastEvent): void {
    this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(event) }));
  }
}

describe("startEventStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSource.instances = [];
  });
  afterEach(() => vi.useRealTimers());

  it("subscribes to the six events, tracks the connection and polls while reconnecting", () => {
    const client = new QueryClient();
    const spy = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const stop = startEventStream(client, {
      createSource: (url) => new FakeSource(url) as unknown as EventSourceLike,
      pollIntervalMs: 1000,
    });
    const source = FakeSource.instances[0];
    expect(source?.url).toBe(OPS.subscribe_events[1]);
    expect([...(source?.listeners.keys() ?? [])]).toEqual([
      "job",
      "progress",
      "feed",
      "item",
      "request",
      "resync",
    ]);
    expect(getConnectionStatus()).toBe("connecting");

    source?.onopen?.();
    expect(getConnectionStatus()).toBe("open");
    expect(spy).not.toHaveBeenCalled();

    source?.emit("feed", {
      event: "feed",
      data: { feed_id: "m1", revision: 3, reason: "refresh" },
    });
    vi.advanceTimersByTime(250);
    expect(spy).toHaveBeenCalledWith({ queryKey: [OPS.list_feeds[0], OPS.list_feeds[1]] });

    source?.onerror?.();
    expect(getConnectionStatus()).toBe("reconnecting");
    spy.mockClear();
    vi.advanceTimersByTime(2000);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy).toHaveBeenCalledWith({ refetchType: "active" });

    // A reconnect refetches everything once and stops the poll.
    spy.mockClear();
    source?.onopen?.();
    expect(getConnectionStatus()).toBe("open");
    expect(spy).toHaveBeenCalledWith();
    spy.mockClear();
    vi.advanceTimersByTime(3000);
    expect(spy).not.toHaveBeenCalled();

    stop();
    expect(source?.closed).toBe(true);
  });
});
