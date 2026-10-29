import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { bucket, mirror } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { resetPlayerForTests, setShortcuts } from "@/stores/player";

afterEach(() => {
  resetPlayerForTests();
});

function handlers() {
  return [
    http.get("/api/feeds", () =>
      HttpResponse.json({
        feeds: [mirror({ id: "mirror-1", title: "Example Podcast" }), bucket({ id: "bucket-1" })],
      }),
    ),
    http.get("/api/feeds/:feedId/items", () =>
      HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
    ),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.get("/api/about", () =>
      HttpResponse.json({
        version: "1.0.0",
        engine: {
          name: "yt-dlp",
          version: "1",
          channel: "stable",
          release_date: null,
          git_head: null,
        },
        ffmpeg_version: "8",
        base_url: "http://localhost:8080",
        layout_version: "1",
        totals: { feeds: 0, episodes: 0, storage_bytes: 0 },
      }),
    ),
    http.get("/healthz/ready", () => HttpResponse.json({ status: "ok", checks: {} })),
    http.get("/api/search/podcasts", () => HttpResponse.json({ query: "atp", results: [] })),
    http.get("/api/search/videos", () => HttpResponse.json({ query: "atp", results: [] })),
  ];
}

describe("AppShell", () => {
  it("opens the command palette with ⌘K and hands free text to the podcast search", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    const { history } = renderApp("/about");
    await screen.findByRole("heading", { name: "About" });

    await user.keyboard("{Meta>}k{/Meta}");
    const palette = await screen.findByRole("dialog", { name: "Command palette" });
    const input = within(palette).getByPlaceholderText(/Search feeds/);
    await user.type(input, "Accidental Tech");
    await user.click(await within(palette).findByText(/Find a podcast named “Accidental Tech”/));
    const dialog = await screen.findByRole("dialog", { name: "Find a podcast" });
    expect(within(dialog).getByLabelText("Podcast or video name")).toHaveValue("Accidental Tech");
    expect(history.location.pathname).toBe("/about");
  });

  it("offers Mirror / Send to Local feed for a URL and jumps to feeds", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    const { history } = renderApp("/about");
    await screen.findByRole("heading", { name: "About" });

    await user.click(screen.getByRole("button", { name: "Open command palette" }));
    const palette = await screen.findByRole("dialog", { name: "Command palette" });
    await user.type(within(palette).getByPlaceholderText(/Search feeds/), "https://x.example/f");
    expect(within(palette).getByText("Mirror this URL")).toBeInTheDocument();
    // The banner holds theme, palette and the live-update dot; no Add Source button.
    expect(screen.queryByRole("button", { name: /Add Source/ })).not.toBeInTheDocument();
    await user.click(within(palette).getByText("Send to Local feed…"));
    await waitFor(() => expect(history.location.pathname).toBe("/"));
    expect(history.location.search).toContain("url=https%3A%2F%2Fx.example%2Ff");

    await user.keyboard("{Control>}k{/Control}");
    const again = await screen.findByRole("dialog", { name: "Command palette" });
    await user.click(await within(again).findByText("Example Podcast"));
    await waitFor(() => expect(history.location.pathname).toBe("/feed/mirror-1"));
    // A feed's page belongs to the Feeds tab.
    const [feedsLink] = screen.getAllByRole("link", { name: "Feeds" });
    expect(feedsLink).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("link", { name: /^Jobs/ })[0]).not.toHaveAttribute("aria-current");
  });

  it("shows the shortcuts sheet on ? and closes it on Escape", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    renderApp("/about");
    await screen.findByRole("heading", { name: "About" });

    await user.keyboard("?");
    const sheet = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(sheet).getByText("Focus the filter of the current table")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("ignores ? and / with single-key shortcuts off, and opens the sheet from the palette", async () => {
    server.use(...handlers());
    setShortcuts(false);
    const user = userEvent.setup();
    renderApp("/about");
    await screen.findByRole("heading", { name: "About" });

    await user.keyboard("?");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.keyboard("{Control>}k{/Control}");
    const palette = await screen.findByRole("dialog", { name: "Command palette" });
    await user.click(within(palette).getByText("Keyboard shortcuts"));
    const sheet = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(sheet).getByRole("switch", { name: "Single-key shortcuts" })).not.toBeChecked();
  });
});
