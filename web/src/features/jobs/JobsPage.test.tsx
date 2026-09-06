import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { applyEvent, Invalidator } from "@/api/events";
import type { JobRead } from "@/api/types";
import { inbox, item, job, mirror } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { recordProgress, resetProgress } from "@/stores/progress";

const feeds = [
  mirror({ id: "mirror-1", title: "Example Podcast" }),
  inbox({ id: "inbox-1", title: "Copycast", name: "Copycast" }),
];

function jobsHandlers(jobs: JobRead[], cancelled: string[] = []) {
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds })),
    http.get("/api/feeds/:feedId/items/:itemId", ({ params }) =>
      HttpResponse.json(
        item({ id: String(params.itemId), feed_id: String(params.feedId), title: "Episode 7" }),
      ),
    ),
    http.get("/api/jobs", ({ request }) => {
      const wanted = new URL(request.url).searchParams.getAll("status");
      const matching = jobs.filter((j) => wanted.length === 0 || wanted.includes(j.status));
      return HttpResponse.json({
        jobs: matching,
        total: matching.length,
        limit: 200,
        offset: 0,
      });
    }),
    http.post("/api/jobs/:jobId/cancel", ({ params }) => {
      const id = String(params.jobId);
      cancelled.push(id);
      const target = jobs.find((j) => j.id === id);
      return HttpResponse.json(
        job({ ...target, status: target?.status === "running" ? "running" : "cancelled" }),
      );
    }),
  ];
}

describe("JobsPage", () => {
  it("shows running cards with live progress, the queue and the history", async () => {
    resetProgress();
    const running = job({
      id: "00000000-0000-4000-8000-000000000001",
      kind: "archive_item",
      status: "running",
      feed_id: "mirror-1",
      item_id: "item-7",
      started_at: "2024-01-15T09:00:00Z",
    });
    const queued = job({
      id: "00000000-0000-4000-8000-000000000002",
      kind: "refresh",
      status: "queued",
      feed_id: "inbox-1",
      trigger: "scheduled",
      attempt: 1,
      error: "HTTP 503",
    });
    const done = job({
      id: "00000000-0000-4000-8000-000000000003",
      kind: "refresh",
      status: "failed",
      feed_id: "mirror-1",
      error: "Source unreachable",
      error_kind: "transient",
      started_at: "2024-01-15T08:00:00Z",
      finished_at: "2024-01-15T08:00:05Z",
    });
    const cancelled: string[] = [];
    server.use(...jobsHandlers([running, queued, done], cancelled));
    const user = userEvent.setup();
    const { client } = renderApp("/jobs");

    const card = await screen.findByTestId("job-card");
    expect(card).toHaveTextContent("Archive · Running");
    expect(await within(card).findByRole("link", { name: "Example Podcast" })).toHaveAttribute(
      "href",
      "/mirrors/mirror-1",
    );
    expect(await within(card).findByRole("link", { name: "Episode 7" })).toBeInTheDocument();

    recordProgress({
      jobId: running.id,
      feedId: "mirror-1",
      itemId: "item-7",
      progress: {
        phase: "downloading",
        item_id: "item-7",
        downloaded_bytes: 5_000_000,
        total_bytes: 10_000_000,
        percent: 50,
        speed_bps: 1_000_000,
        eta_seconds: 5,
      },
    });
    await waitFor(() => expect(card).toHaveTextContent("50%"));
    expect(card).toHaveTextContent("Downloading");
    expect(card).toHaveTextContent("1.0 MB/s");

    const queuedRow = screen.getByTestId("queued-job");
    expect(queuedRow).toHaveTextContent("Refresh");
    expect(queuedRow).toHaveTextContent("retry 1: HTTP 503");
    expect(within(queuedRow).getByRole("link", { name: "Copycast" })).toHaveAttribute(
      "href",
      "/inboxes/inbox-1",
    );

    const row = await screen.findByTestId("job-row");
    expect(row).toHaveTextContent("Failed");
    expect(row).toHaveTextContent("Source unreachable");
    expect(row).toHaveTextContent("0:05");

    await user.click(within(card).getByRole("button", { name: /Cancel archive_item job/ }));
    await waitFor(() => expect(cancelled).toEqual([running.id]));
    expect(await screen.findByText("Cancellation requested")).toBeInTheDocument();

    // A `job` event for the finished job refetches the lists.
    const updates = () =>
      client
        .getQueryCache()
        .findAll({ queryKey: ["get", "/api/jobs"] })
        .reduce((sum, query) => sum + query.state.dataUpdateCount, 0);
    const before = updates();
    applyEvent(
      { event: "job", data: { job: { ...running, status: "cancelled" } } },
      new Invalidator(client, 0),
    );
    await waitFor(() => expect(updates()).toBeGreaterThan(before));
  });

  it("shows an idle state when nothing runs or waits", async () => {
    server.use(...jobsHandlers([]));
    renderApp("/jobs");
    expect(await screen.findByText("The worker is idle")).toBeInTheDocument();
    expect(await screen.findByText("No job has finished yet.")).toBeInTheDocument();
  });
});
