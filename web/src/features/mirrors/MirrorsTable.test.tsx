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
    expect(await screen.findByRole("heading", { name: "Feeds", level: 2 })).toBeInTheDocument();
    const row = await screen.findByTestId("mirror-row");
    expect(within(row).getByRole("link", { name: "Example Podcast" })).toHaveAttribute(
      "href",
      "/mirrors/mirror-1",
    );
    expect(within(row).getAllByText("Podcast RSS")[0]).toBeInTheDocument();
    expect(within(row).getByText("Refreshed", { exact: false })).toBeInTheDocument();
    await user.click(within(row).getAllByRole("button", { name: "Copy feed URL" })[0]!);
    expect(await screen.findByText("Feed URL copied")).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe(feed.feed_url);
  });

  it("lists a Bucket Feed as a Local row that opens its bucket page", async () => {
    server.use(...handlers([feed, copycast, later]));
    const user = userEvent.setup();
    renderApp("/");
    const rows = await screen.findAllByTestId("bucket-row");
    expect(rows).toHaveLength(2);
    const row = rows[0]!;
    expect(within(row).getByRole("link", { name: "Copycast" })).toHaveAttribute(
      "href",
      "/buckets/bucket-1",
    );
    expect(within(row).getByText("Local", { selector: "[data-slot=badge]" })).toBeInTheDocument();
    expect(within(row).getByText("Local", { selector: ".sr-only" })).toBeInTheDocument();
    expect(row).toHaveTextContent("2");
    expect(within(row).getAllByText("—")).toHaveLength(2);

    // The default Bucket only opens; another one can be deleted from its menu.
    await user.click(within(row).getByRole("button", { name: "More actions for Copycast" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Open" })).toHaveAttribute(
      "href",
      "/buckets/bucket-1",
    );
    expect(within(menu).queryByRole("menuitem", { name: "Delete…" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(within(rows[1]!).getByRole("button", { name: "More actions for Later" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    expect(
      await screen.findByRole("alertdialog", { name: "Delete Bucket “Later”?" }),
    ).toBeInTheDocument();
  });

  it("shows the empty state only when there is neither a Mirror nor a Bucket", async () => {
    server.use(
      ...handlers([mirror({ id: "mirror-wl", title: "Watch Later", playlist_capture: true })]),
    );
    renderApp("/");
    expect(await screen.findByText("No Feeds yet")).toBeInTheDocument();
    expect(screen.queryByTestId("mirror-row")).not.toBeInTheDocument();
  });
});
