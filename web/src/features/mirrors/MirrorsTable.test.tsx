import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { FeedRead } from "@/api/types";
import { bucket, mirror } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const feed = mirror({
  id: "mirror-1",
  title: "Example Podcast",
  service: "Podcast RSS",
  episode_count: 3,
  counts: { listed: 3, available: 2, delisted: 1, wanted: 0, archived: 3, failed: 0 },
});
const watchLater = mirror({
  id: "mirror-wl",
  title: "Watch Later",
  service: "YouTube",
  source_kind: "ytdlp",
  playlist_capture: true,
  sync_deletions: true,
});
const copycast = bucket({
  id: "bucket-1",
  name: "Copycast",
  title: "Copycast",
  episode_count: 2,
  request_count: 2,
  is_default: true,
});
const later = bucket({ id: "bucket-2", name: "Later", title: "Later" });

function handlers(feeds: FeedRead[]) {
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds })),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 500, offset: 0 })),
  ];
}

describe("Feeds table", () => {
  it("renders a Mirror row with health, service, counts and copies the feed URL", async () => {
    server.use(...handlers([feed, copycast]));
    // user-event installs a clipboard stub for the test; read it back after the click.
    const user = userEvent.setup();
    renderApp("/");
    const row = (await screen.findAllByTestId("mirror-row"))[0]!;
    // No Episodes column any more.
    expect(screen.queryByRole("columnheader", { name: "Episodes" })).not.toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Example Podcast" })).toHaveAttribute(
      "href",
      "/feed/mirror-1",
    );
    expect(within(row).getAllByText("Podcast RSS")[0]).toBeInTheDocument();
    expect(within(row).getByText("Refreshed", { exact: false })).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Copy feed URL" }));
    expect(await screen.findByText("Feed URL copied")).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe(feed.feed_url);
  });

  it("lists a captured YouTube playlist as a Mirror row", async () => {
    server.use(...handlers([feed, watchLater]));
    renderApp("/");
    const rows = await screen.findAllByTestId("mirror-row");
    expect(rows).toHaveLength(2);
    const row = rows[1]!;
    expect(within(row).getByRole("link", { name: "Watch Later" })).toHaveAttribute(
      "href",
      "/feed/mirror-wl",
    );
    expect(within(row).getAllByText("YouTube")[0]).toBeInTheDocument();
  });

  it("puts a Delete button on each row and no menu", async () => {
    server.use(...handlers([feed, copycast]));
    const user = userEvent.setup();
    renderApp("/");
    const row = (await screen.findAllByTestId("mirror-row"))[0]!;
    expect(within(row).queryByRole("button", { name: /More actions/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /More actions/ })).not.toBeInTheDocument();
    for (const name of ["Copy feed URL", "Refresh now", "Pause"]) {
      expect(within(row).getByRole("button", { name })).toBeInTheDocument();
    }
    await user.click(within(row).getByRole("button", { name: "Delete Example Podcast" }));
    const dialog = await screen.findByRole("alertdialog", {
      name: "Delete Mirror “Example Podcast”?",
    });
    await user.click(within(dialog).getByRole("button", { name: "Keep it" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("lists a Local feed as a Local row that opens its feed page", async () => {
    server.use(...handlers([feed, copycast, later]));
    const user = userEvent.setup();
    renderApp("/");
    const rows = await screen.findAllByTestId("local-feed-row");
    expect(rows).toHaveLength(2);
    const row = rows[0]!;
    expect(within(row).getByRole("link", { name: "Copycast" })).toHaveAttribute(
      "href",
      "/feed/bucket-1",
    );
    expect(within(row).getByText("Local", { selector: "[data-slot=badge]" })).toBeInTheDocument();
    expect(within(row).getByText("Local", { selector: ".sr-only" })).toBeInTheDocument();
    expect(within(row).getAllByText("—")).toHaveLength(2);
    expect(within(row).getByRole("button", { name: "Copy feed URL" })).toBeInTheDocument();

    // The default Local feed cannot be deleted; another one can.
    const locked = within(row).getByRole("button", { name: "Delete Copycast" });
    expect(locked).toHaveAttribute("aria-disabled", "true");
    await user.hover(locked);
    expect(
      (await screen.findAllByText("The default Local feed cannot be deleted"))[0],
    ).toBeInTheDocument();
    await user.click(locked);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await user.click(within(rows[1]!).getByRole("button", { name: "Delete Later" }));
    expect(
      await screen.findByRole("alertdialog", { name: "Delete Local feed “Later”?" }),
    ).toBeInTheDocument();
  });

  it("shows the empty state only when there is no Feed at all", async () => {
    server.use(...handlers([]));
    renderApp("/");
    expect(await screen.findByText("No Feeds yet")).toBeInTheDocument();
    expect(screen.queryByTestId("mirror-row")).not.toBeInTheDocument();
  });
});
