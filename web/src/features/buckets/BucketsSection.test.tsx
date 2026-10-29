import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { StorageSummary } from "@/api/types";
import { bucket, mirror } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { BUCKETS_OPEN_KEY } from "./BucketsSection";

const podcast = mirror({ id: "mirror-1", title: "Example Podcast", storage_bytes: 12_345_678 });
const copycast = bucket({
  id: "bucket-1",
  name: "Copycast",
  title: "Copycast",
  episode_count: 2,
  request_count: 2,
  storage_bytes: 8_800,
  is_default: true,
});
const empty = bucket({ id: "bucket-2", name: "Later", title: "Later", request_count: 0 });
const watchLater = mirror({
  id: "mirror-wl",
  title: "Watch Later",
  playlist_capture: true,
  storage_bytes: 0,
});

/** What the Object store holds: the files counted per Feed, sizes in bytes. */
const storage: StorageSummary = {
  feeds: [
    { feed_id: "mirror-1", file_count: 43, total_bytes: 554_000_000 },
    { feed_id: "bucket-1", file_count: 1, total_bytes: 9_000 },
    { feed_id: "bucket-2", file_count: 0, total_bytes: 0 },
    { feed_id: "mirror-wl", file_count: 2, total_bytes: 1_000 },
  ],
  file_count: 46,
  total_bytes: 554_010_000,
};

function handlers({ summary = storage, wait = false } = {}) {
  return [
    // The table's order (here: as the API sorted them) is the cards' order.
    http.get("/api/feeds", () =>
      HttpResponse.json({ feeds: [podcast, copycast, empty, watchLater] }),
    ),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 500, offset: 0 })),
    http.get("/api/storage", async () => {
      if (wait) await delay("infinite");
      return HttpResponse.json(summary);
    }),
  ];
}

describe("Buckets on the Feeds page", () => {
  it("keeps the box collapsed until its header is clicked, then shows one bucket per Feed", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    renderApp("/");

    // The table is up; the box below shows how many buckets and their size (from the feeds),
    // and lists the Object store only once opened.
    await screen.findAllByTestId("mirror-row");
    const section = screen.getByRole("region", { name: "Buckets" });
    const header = within(section).getByRole("button", { name: /Buckets/ });
    expect(header).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(header).toHaveTextContent(/^Buckets4 buckets · [\d.]+ MB$/));
    expect(screen.queryAllByTestId("bucket-card")).toHaveLength(0);
    // No more creating a bucket or capturing a playlist from here.
    expect(within(section).getAllByRole("button")).toEqual([header]);

    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    await waitFor(() => expect(header).toHaveTextContent("4 buckets · 46 files · 554 MB"));
    const cards = await screen.findAllByTestId("bucket-card");
    // In the table's order; every card is a Feed's bucket page.
    expect(cards.map((card) => card.textContent)).toEqual([
      expect.stringContaining("Example Podcast"),
      expect.stringContaining("Copycast"),
      expect.stringContaining("Later"),
      expect.stringContaining("Watch Later"),
    ]);
    expect(cards[0]).toHaveAttribute("href", "/buckets/mirror-1");
    expect(cards[1]).toHaveAttribute("href", "/buckets/bucket-1");
    expect(cards[3]).toHaveAttribute("href", "/buckets/mirror-wl");
    // The files counted in the Object store, not the Episodes; no sync badge.
    expect(cards[0]).toHaveTextContent("43 files · 554 MB");
    expect(cards[1]).toHaveTextContent("1 file · 0.0 MB");
    expect(cards[2]).toHaveTextContent("0 files · 0.0 MB");
    for (const card of cards) {
      expect(card).not.toHaveTextContent(/Synced|Episode/);
      expect(card.querySelector("[data-sync]")).toBeNull();
    }
    expect(window.localStorage.getItem(BUCKETS_OPEN_KEY)).toBe("1");

    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryAllByTestId("bucket-card")).toHaveLength(0));
    expect(window.localStorage.getItem(BUCKETS_OPEN_KEY)).toBe("0");
  });

  it("shows the Feeds' own sizes while the files are being counted", async () => {
    server.use(...handlers({ wait: true }));
    renderApp("/#buckets");
    const cards = await screen.findAllByTestId("bucket-card");
    expect(cards[0]).toHaveTextContent("… files · 12 MB");
    expect(screen.getByRole("button", { name: /Buckets/ })).toHaveTextContent("4 buckets · 12 MB");
  });

  it("opens for a URL to send and keeps it on the Local feeds' cards only", async () => {
    server.use(...handlers());
    renderApp("/?url=https%3A%2F%2Fexample.org%2Fvideo");

    const cards = await screen.findAllByTestId("bucket-card");
    expect(
      screen.getByText("Pick the Local feed that should receive the URL you pasted."),
    ).toBeVisible();
    // Only a Local feed takes a URL: the Mirrors' buckets stay out while one is to be sent, and
    // each card opens the Local feed's page with its Add URL form filled in.
    expect(cards).toHaveLength(2);
    const hrefs = cards.map((card) => card.getAttribute("href") ?? "");
    expect(hrefs[0]).toMatch(/^\/feed\/bucket-1\?url=https(%3A|:)%2F%2Fexample\.org%2Fvideo$/);
    expect(hrefs[1]).toMatch(/^\/feed\/bucket-2\?url=/);
  });

  it("opens when the page is entered at #buckets", async () => {
    server.use(...handlers());
    renderApp("/#buckets");
    expect(await screen.findAllByTestId("bucket-card")).toHaveLength(4);
    expect(screen.getByRole("button", { name: /Buckets/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });
});
