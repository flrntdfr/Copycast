import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { MirrorUpdate } from "@/api/types";
import { item, job, mirror, problem, selectionResult } from "@/test/factories";
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
  last_light_refresh_at: "2024-01-15T09:30:00Z",
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
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 100, offset: 0 })),
    http.post("/api/mirrors/:feedId/refresh", async ({ request }) => {
      await record(request);
      return HttpResponse.json(job({ status: "queued" }), { status: 202 });
    }),
    http.post("/api/mirrors/:feedId/pause", async ({ request }) => {
      await record(request);
      return HttpResponse.json({ ...feed, paused: true });
    }),
    http.post("/api/feeds/:feedId/archive-available", async ({ request }) => {
      await record(request);
      return HttpResponse.json(selectionResult({ resolved: ["a", "b"], jobs: [job(), job()] }), {
        status: 202,
      });
    }),
    http.post("/api/feeds/:feedId/retry-failed", async ({ request }) => {
      await record(request);
      return HttpResponse.json(selectionResult({ resolved: ["c"], jobs: [job()] }), {
        status: 202,
      });
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

/** Click the header's Settings button and return the form inside the dialog. */
async function openSettings(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "Example Podcast" });
  await user.click(screen.getByRole("button", { name: "Settings" }));
  const dialog = await screen.findByRole("dialog", { name: "Mirror settings" });
  return within(dialog).getByRole("form", { name: "Mirror settings" });
}

describe("Mirror page", () => {
  it("shows the header with health, counts, feed URL and the Catalog directly", async () => {
    const calls: { method: string; path: string }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");

    expect(await screen.findByRole("heading", { name: "Example Podcast" })).toBeInTheDocument();
    expect(screen.getByText("Podcast RSS")).toBeInTheDocument();
    expect(screen.getByText(/Refreshed .* ago/)).toBeInTheDocument();
    expect(screen.getByText(/Checked on fetch/)).toHaveTextContent(/Checked on fetch .* ago/);
    expect(screen.getByText(/3 Episodes · 2 Available · 1 Delisted · 12.3 MB/)).toBeInTheDocument();
    expect(screen.getByLabelText("Mirror Feed URL")).toHaveValue(feed.feed_url);
    expect(await screen.findByText("First")).toBeInTheDocument();

    // No tabs, no credentials, no range archive: the Catalog is the page.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText(/Username and password/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("textbox", { name: "Archive by episode numbers" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Filter by title" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();

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

  it("archives everything Available from the menu, optionally switching to Archive", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(
      // Before the shared handlers: the first matching handler wins.
      http.get("/api/feeds/:feedId", () =>
        HttpResponse.json({
          ...feed,
          backfill: { mode: "automatic", latest_n: null, retention_days: 7 },
          counts: { ...feed.counts, failed: 1 },
        }),
      ),
      ...handlers(calls),
    );
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");
    await screen.findByRole("heading", { name: "Example Podcast" });

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Archive all Available…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Archive 2 Available Episodes now?");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Archive 2" }));
    await waitFor(() =>
      expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(
        expect.arrayContaining([
          "POST /api/feeds/mirror-1/archive-available",
          "PATCH /api/mirrors/mirror-1",
        ]),
      ),
    );
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ backfill: { mode: "all" } });
    expect(await screen.findByText("2 Episodes queued")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Retry 1 failed" }));
    await waitFor(() =>
      expect(calls).toContainEqual(
        expect.objectContaining({ path: "/api/feeds/mirror-1/retry-failed" }),
      ),
    );
    expect(await screen.findByText("1 failed download queued again")).toBeInTheDocument();
  });

  it("edits the title in place from the pen button", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");
    await screen.findByRole("heading", { name: "Example Podcast" });
    await user.click(screen.getByRole("button", { name: "Edit title" }));
    const field = screen.getByRole("textbox", { name: "Title" });
    expect(field).toHaveValue("Example Podcast");
    await user.clear(field);
    await user.type(field, "My Podcast{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ title: "My Podcast" });
    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: "Title" })).not.toBeInTheDocument(),
    );
    // Escape leaves the title as it was.
    await user.click(screen.getByRole("button", { name: "Edit title" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "Title" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
  });

  it("shows the feed URL as a QR code only when asked", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");
    await screen.findByRole("heading", { name: "Example Podcast" });
    expect(screen.queryByTestId("qr-code")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show mirror feed url as a QR code" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("qr-code")).toBeInTheDocument();
    expect(within(dialog).getByTestId("qr-code").querySelector("svg")).not.toBeNull();
    expect(dialog).toHaveTextContent(feed.feed_url);
  });

  it("opens the Settings dialog from the header button and from the menu", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");
    await screen.findByRole("heading", { name: "Example Podcast" });
    expect(screen.queryByRole("dialog", { name: "Mirror settings" })).not.toBeInTheDocument();

    // The actions row reads Settings, Refresh now, Pause, left to right.
    const buttons = screen.getAllByRole("button").map((button) => button.textContent?.trim());
    const settingsAt = buttons.indexOf("Settings");
    expect(settingsAt).toBeGreaterThan(-1);
    expect(buttons[settingsAt + 1]).toBe("Refresh now");
    expect(buttons[settingsAt + 2]).toBe("Pause");

    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Mirror settings" });
    expect(dialog).toHaveTextContent("Example Podcast");
    expect(within(dialog).getByRole("form", { name: "Mirror settings" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Mirror settings" })).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Change Source URL…" }));
    const reopened = await screen.findByRole("dialog", { name: "Mirror settings" });
    expect(within(reopened).getByLabelText("Source URL")).toHaveValue(feed.source_url);
  });

  it("opens the Settings dialog once from ?settings=true and cleans the URL", async () => {
    server.use(...handlers());
    const { history } = renderApp("/mirrors/mirror-1?settings=true");
    const dialog = await screen.findByRole("dialog", { name: "Mirror settings" });
    expect(within(dialog).getByRole("form", { name: "Mirror settings" })).toBeInTheDocument();
    await waitFor(() => expect(history.location.search).toBe(""));
    expect(history.location.pathname).toBe("/mirrors/mirror-1");
  });

  it("saves only the changed settings and shows rejected engine options per key", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");

    const form = await openSettings(user);
    expect(within(form).getByLabelText("Source URL")).toHaveValue(feed.source_url);
    expect(within(form).getByText(/None\. Copycast never deletes/)).toBeInTheDocument();

    // Overrides: empty fields follow the defaults from Settings and say so.
    await waitFor(() =>
      expect(within(form).getByLabelText("Metadata language")).toHaveAttribute("placeholder", "fr"),
    );
    expect(within(form).getByText("Using the default: fr.")).toBeInTheDocument();
    expect(within(form).getByText("Using the default: 5.")).toBeInTheDocument();
    expect(within(form).queryByRole("button", { name: "Use default" })).not.toBeInTheDocument();

    // The title follows the Source until you type your own.
    expect(within(form).getByLabelText("Title")).toHaveAttribute("placeholder", feed.source_title);
    expect(
      within(form).getByText(`Using the Source's title: ${feed.source_title}.`),
    ).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Title"), "Mine");
    expect(within(form).getByRole("button", { name: "Use Source title" })).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Use Source title" }));
    expect(within(form).getByLabelText("Title")).toHaveValue("");

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

  it("offers Ephemeral and Archive, with Backfill as Archive's switch", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");
    const form = await openSettings(user);
    expect(within(form).getByRole("tab", { name: "Archive" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Rolling N, Selection and Latest N only show on a Mirror that still uses them.
    expect(
      within(form)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Ephemeral", "Archive"]);
    const backfill = within(form).getByRole("switch", { name: /Backfill/ });
    expect(backfill).toBeChecked();

    // A change that deletes nothing goes straight through.
    await user.click(within(form).getByRole("tab", { name: "Ephemeral" }));
    expect(within(form).getByLabelText("Keep downloaded for")).toHaveValue(7);
    expect(within(form).getByText(/expire 7 days after/)).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.map((c) => c.method).filter((m) => m !== "GET")).toEqual(["POST", "PATCH"]);
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      backfill: { mode: "automatic", retention_days: 7 },
    });

    // Choosing Archive starts with Backfill off (the form shows the stored policy again).
    await user.click(within(form).getByRole("tab", { name: "Ephemeral" }));
    await user.click(within(form).getByRole("tab", { name: "Archive" }));
    expect(within(form).getByRole("switch", { name: /Backfill/ })).not.toBeChecked();
    expect(
      within(form).getByText("Off: only what the Source publishes from now on."),
    ).toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1]?.body).toEqual({
      backfill: { mode: "new" },
    });
  });

  it("keeps a Rolling Mirror's tab and asks before archived Episodes are deleted", async () => {
    const calls: { method: string; path: string; body?: unknown }[] = [];
    server.use(
      // Before the shared handlers: the first matching handler wins.
      http.get("/api/feeds/:feedId", () =>
        HttpResponse.json({
          ...feed,
          backfill: { mode: "rolling", latest_n: 3, retention_days: null },
        }),
      ),
      ...handlers(calls),
    );
    const user = userEvent.setup();
    renderApp("/mirrors/mirror-1");
    const form = await openSettings(user);
    expect(within(form).getByRole("tab", { name: "Rolling N" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    // Rolling 1 would delete two of the three archived Episodes: confirm first.
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
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);

    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Delete and switch",
      }),
    );
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      backfill: { mode: "rolling", latest_n: 1 },
    });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("deletes the Mirror from the Danger zone and returns to the list", async () => {
    const calls: { method: string; path: string }[] = [];
    server.use(...handlers(calls));
    const user = userEvent.setup();
    const { history } = renderApp("/mirrors/mirror-1");
    await openSettings(user);
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
    await waitFor(() => expect(history.location.pathname).toBe("/"));
  });
});
