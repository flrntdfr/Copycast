import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { describeWorkerSeen, workerSeenOf } from "./worker-status";
import { about, ready } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

function handlers(readiness = ready(), readyStatus = 200) {
  return [
    http.get("/api/about", () => HttpResponse.json(about())),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.get("/healthz/ready", () => HttpResponse.json(readiness, { status: readyStatus })),
  ];
}

describe("worker-status helpers", () => {
  it("interprets the readiness check", () => {
    const now = new Date("2024-01-15T09:05:00Z");
    expect(workerSeenOf(undefined)).toEqual({ kind: "unknown" });
    expect(workerSeenOf(ready())).toEqual({
      kind: "seen",
      at: "2024-01-15T09:00:00Z",
      stale: false,
    });
    const stale = workerSeenOf(
      ready({ checks: { worker_seen_at: { ok: false, detail: "2024-01-15T09:00:00Z" } } }),
    );
    expect(stale).toEqual({ kind: "seen", at: "2024-01-15T09:00:00Z", stale: true });
    expect(describeWorkerSeen(stale, now)).toBe("5 minutes ago (not running?)");
    expect(
      describeWorkerSeen(
        workerSeenOf(ready({ checks: { worker_seen_at: { ok: false, detail: "never" } } })),
      ),
    ).toBe("never");
    expect(
      describeWorkerSeen(
        workerSeenOf(
          ready({ checks: { worker_seen_at: { ok: false, detail: "database unavailable" } } }),
        ),
      ),
    ).toBe("unknown (database unavailable)");
  });
});

describe("AboutPage", () => {
  it("shows versions, totals and the worker heartbeat", async () => {
    server.use(...handlers());
    renderApp("/about");

    expect(await screen.findByRole("heading", { name: "About" })).toBeInTheDocument();
    expect(screen.getByText("Copycast 1.0.0")).toBeInTheDocument();
    const engine = screen.getByTestId("about-engine");
    expect(engine).toHaveTextContent("yt-dlp 2026.8.19 (nightly)");
    expect(engine).toHaveTextContent("released");
    expect(engine).toHaveTextContent("abc1234");
    expect(screen.getByText("8.1.2")).toBeInTheDocument();
    expect(screen.getByText(/2 feeds · 42 Episodes · 1\.5 GB/)).toBeInTheDocument();

    const worker = screen.getByTestId("about-worker");
    await waitFor(() => expect(worker).not.toHaveTextContent("…"));
    expect(within(worker).getByRole("time")).toHaveAttribute("datetime", "2024-01-15T09:00:00Z");
    expect(screen.queryByText("The worker has not reported recently")).not.toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Export OPML" })).toHaveAttribute(
      "href",
      "/api/feeds.opml",
    );
    expect(screen.getByRole("link", { name: "GitHub" })).toHaveAttribute(
      "href",
      "https://github.com/flrntdfr/Copycast",
    );

    // The storage table and the purge button are gone: buckets live on the Feeds page.
    expect(screen.queryByRole("heading", { name: "Storage per feed" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("storage-row")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Delete every archived Episode" }),
    ).not.toBeInTheDocument();
  });

  it("warns when the worker was never seen, even though readiness answers 503", async () => {
    server.use(
      ...handlers(ready({ checks: { worker_seen_at: { ok: false, detail: "never" } } }), 503),
    );
    renderApp("/about");
    const worker = await screen.findByTestId("about-worker");
    await waitFor(() => expect(worker).toHaveTextContent("never"));
    expect(screen.getByText("The worker has not reported recently")).toBeInTheDocument();
  });

  it("flags a missing ffmpeg", async () => {
    server.use(
      http.get("/api/about", () => HttpResponse.json(about({ ffmpeg_version: null }))),
      ...handlers(),
    );
    renderApp("/about");
    expect(await screen.findByText("ffmpeg is missing")).toBeInTheDocument();
    expect(screen.getByText("missing")).toBeInTheDocument();
  });
});
