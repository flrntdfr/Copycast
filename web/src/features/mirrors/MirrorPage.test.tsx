import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { MirrorUpdate } from "@/api/types";
import { item, job, mirror, problem } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const feed = mirror({
  id: "mirror-1",
  title: "Example Podcast",
  service: "Podcast RSS",
  storage_bytes: 12_345_678,
  episode_count: 3,
  counts: { listed: 3, available: 2, delisted: 1, wanted: 0, archived: 3, failed: 0 },
  engine_options: { format: "bestaudio" },
});

function handlers(calls: { method: string; path: string; body?: unknown }[] = []) {
  const record = async (request: Request) => {
    const url = new URL(request.url);
    let body: unknown;
    if (request.method !== "GET" && request.method !== "DELETE") {
      const text = await request.text();
      body = text ? (JSON.parse(text) as unknown) : undefined;
    }
    calls.push({ method: request.method, path: url.pathname, body });
  };
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [feed] })),
    http.get("/api/feeds/:feedId", () => HttpResponse.json(feed)),
    http.get("/api/settings/defaults", () =>
      HttpResponse.json({ language: "fr", min_duration_seconds: 300 }),
    ),
    http.get("/api/feeds/:feedId/items", () =>
      HttpResponse.json({
        items: [item({ id: "item-1", feed_id: feed.id, title: "First" })],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    ),
    http.get("/api/jobs", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("kind") === "refresh") {
        return HttpResponse.json({
          jobs: [
            job({
              status: "succeeded",
              trigger: "scheduled",
              started_at: "2024-01-15T09:00:00Z",
              finished_at: "2024-01-15T09:00:42Z",
              result: { listed: 120, new: 3, delisted: 0, wanted: 3 },
            }),
            job({ status: "failed", trigger: "manual", error: "HTTP 503 from the Source" }),
          ],
          total: 2,
          limit: 50,
          offset: 0,
        });
      }
      return HttpResponse.json({ jobs: [], total: 0, limit: 100, offset: 0 });
    }),
    http.post("/api/mirrors/:feedId/refresh", async ({ request }) => {
      await record(request);
      return HttpResponse.json(job({ status: "queued" }), { status: 202 });
    }),
    http.post("/api/mirrors/:feedId/pause", async ({ request }) => {
      await record(request);
      return HttpResponse.json({ ...feed, paused: true });
    }),
    http.post("/api/mirrors/:feedId/preview", async ({ request }) => {
      await record(request);
      const body = calls.at(-1)?.body as MirrorUpdate;
      const deletes = body.backfill?.mode === "rolling" ? 3 - (body.backfill.latest_n ?? 0) : 0;
      return HttpResponse.json({
        would_delete_count: deletes,
        would_delete_bytes: deletes * 1_000_000,
        would_archive_count: 0,
      });
    }),
    http.patch("/api/mirrors/:feedId", async ({ request }) => {
      await record(request);
      const body = calls.at(-1)?.body as MirrorUpdate;
      if (body.engine_options && "outtmpl" in body.engine_options) {
        return HttpResponse.json(
          problem("engine-option-rejected", 422, { keys: ["outtmpl"], scope: "feed" }),
          {
            status: 422,
            headers: { "Content-Type": "application/problem+json" },
          },
        );
      }
      return HttpResponse.json({ ...feed, ...body, backfill: feed.backfill });
    }),
    http.delete("/api/feeds/:feedId", async ({ request }) => {
      await record(request);
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}

describe("Mirror page", () => {
  it("shows the header with health, counts, feed URL and the Catalog tab", async () => {
    const calls: { method: string; path: string }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");

    expect(await screen.findByRole("heading", { name: "Example Podcast" })).toBeInTheDocument();
    expect(screen.getByText("Podcast RSS")).toBeInTheDocument();
    expect(screen.getByText(/Refreshed .* ago/)).toBeInTheDocument();
    expect(screen.getByText(/3 Episodes · 2 Available · 1 Delisted · 12.3 MB/)).toBeInTheDocument();
    expect(screen.getByLabelText("Mirror Feed URL")).toHaveValue(feed.feed_url);
    expect(await screen.findByText("First")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh now" }));
    await waitFor(() =>
      expect(calls).toContainEqual(
        expect.objectContaining({ path: "/api/mirrors/mirror-1/refresh" }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() =>
      expect(calls).toContainEqual(
        expect.objectContaining({ path: "/api/mirrors/mirror-1/pause" }),
      ),
    );
  });

  it("lists Refreshes with trigger, duration, counts and errors", async () => {
    server.use(...handlers());
    renderApp("/mirrors/mirror-1?tab=refreshes");
    const rows = await screen.findAllByTestId("refresh-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Scheduled");
    expect(rows[0]).toHaveTextContent("0:42");
    expect(rows[0]).toHaveTextContent("120 listed · 3 new · 3 queued");
    expect(rows[1]).toHaveTextContent("Failed");
    expect(rows[1]).toHaveTextContent("HTTP 503 from the Source");
  });

  it("saves only the changed settings and shows rejected engine options per key", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1?tab=settings");

    const form = await screen.findByRole("form", { name: "Mirror settings" });
    expect(within(form).getByLabelText("Source URL")).toHaveValue(feed.source_url);
    expect(within(form).getByText(/None\. Copycast never deletes/)).toBeInTheDocument();

    // Overrides: empty fields follow the defaults from Settings and say so.
    await waitFor(() =>
      expect(within(form).getByLabelText("Metadata language")).toHaveAttribute("placeholder", "fr"),
    );
    expect(within(form).getByText("Using the default: fr.")).toBeInTheDocument();
    expect(within(form).getByText("Using the default: 5.")).toBeInTheDocument();
    expect(within(form).queryByRole("button", { name: "Use default" })).not.toBeInTheDocument();

    await user.click(within(form).getByRole("switch", { name: "Follow" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ follow: false });
    expect(await screen.findByText("Settings saved")).toBeInTheDocument();

    // An own value diverging from the default is marked and can be reset (sent as null).
    await user.type(within(form).getByLabelText("Metadata language"), "en");
    expect(await within(form).findByTestId("settings-language-diverges")).toHaveTextContent(
      "Overrides the default (fr)",
    );
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1]?.body).toMatchObject({
      preferred_language: "en",
    });

    const options = within(form).getByLabelText("Engine options");
    await user.clear(options);
    await user.type(options, '{{"outtmpl": "x"}');
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    const rejected = await screen.findByRole("list", { name: "Rejected engine options" });
    expect(rejected).toHaveTextContent("outtmpl");
    expect(rejected).toHaveTextContent("not allowed for a feed");
  });

  it("previews a mode change and asks before archived Episodes are deleted", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1?tab=settings");
    const form = await screen.findByRole("form", { name: "Mirror settings" });
    expect(within(form).getByRole("tab", { name: "Everything" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Latest N is not offered for a Mirror that does not use it.
    expect(within(form).queryByRole("tab", { name: "Latest N" })).not.toBeInTheDocument();

    // A change that deletes nothing goes straight through.
    await user.click(within(form).getByRole("tab", { name: "Automatic" }));
    expect(within(form).getByLabelText("Keep downloaded for")).toHaveValue(7);
    expect(within(form).getByText(/expire 7 days after/)).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.map((c) => c.method).filter((m) => m !== "GET")).toEqual(["POST", "PATCH"]);
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      backfill: { mode: "automatic", retention_days: 7 },
    });

    // Rolling 1 would delete two of the three archived Episodes: confirm first.
    await user.click(within(form).getByRole("tab", { name: "Rolling N" }));
    const keep = within(form).getByLabelText("Keep the newest");
    await user.clear(keep);
    await user.type(keep, "1");
    expect(within(form).getByText(/outside the newest N are deleted/)).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Delete 2 archived Episodes?");
    expect(dialog).toHaveTextContent("Rolling 1 keeps only the newest 1 Episode.");
    expect(dialog).toHaveTextContent("2 archived Episodes (2.0 MB) will be deleted now");
    await user.click(within(dialog).getByRole("button", { name: "Keep them" }));
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);

    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Delete and switch",
      }),
    );
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1]?.body).toEqual({
      backfill: { mode: "rolling", latest_n: 1 },
    });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("deletes the Mirror from the Danger zone and returns to the list", async () => {
    const calls: { method: string; path: string }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    const { history } = renderApp("/mirrors/mirror-1?tab=settings");
    await user.click(await screen.findByRole("button", { name: "Delete Mirror…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("only copy");
    await user.click(within(dialog).getByRole("button", { name: "Delete Mirror" }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        method: "DELETE",
        path: "/api/feeds/mirror-1",
        body: undefined,
      }),
    );
    await waitFor(() => expect(history.location.pathname).toBe("/mirrors"));
  });
});

describe("Mirrors list", () => {
  it("renders a row with health, service, counts and copies the feed URL", async () => {
    server.use(...handlers());
    // user-event installs a clipboard stub for the test; read it back after the click.
    const user = userEvent.setup();
    renderApp("/mirrors");
    const row = await screen.findByTestId("mirror-row");
    expect(within(row).getByRole("link", { name: "Example Podcast" })).toHaveAttribute(
      "href",
      "/mirrors/mirror-1",
    );
    expect(within(row).getAllByText("Podcast RSS")[0]).toBeInTheDocument();
    await user.click(within(row).getAllByRole("button", { name: "Copy feed URL" })[0]!);
    expect(await screen.findByText("Feed URL copied")).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe(feed.feed_url);
  });

  it("hands a pasted URL to the wizard", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    const { history } = renderApp("/mirrors");
    await user.type(
      await screen.findByLabelText("Source URL"),
      "https://podcast.example/feed.xml{Enter}",
    );
    await waitFor(() => expect(history.location.pathname).toBe("/mirrors/new"));
    expect(history.location.search).toContain("url=");
  });
});
