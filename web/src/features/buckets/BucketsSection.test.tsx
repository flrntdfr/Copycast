import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { BucketCreate } from "@/api/types";
import { bucket, mirror, request } from "@/test/factories";
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

function handlers(state: { created?: BucketCreate[] } = {}) {
  return [
    http.get("/api/feeds", () =>
      HttpResponse.json({ feeds: [podcast, copycast, empty, watchLater] }),
    ),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 500, offset: 0 })),
    http.get("/api/buckets/:bucketId/requests", () =>
      HttpResponse.json({
        requests: [
          request({ status: "expanded", item_count: 3, created_at: "2024-01-15T09:00:00Z" }),
        ],
        total: 1,
        limit: 1,
        offset: 0,
      }),
    ),
    http.post("/api/buckets", async ({ request: req }) => {
      const body = (await req.json()) as BucketCreate;
      state.created?.push(body);
      return HttpResponse.json(bucket({ id: "bucket-3", name: body.name, title: body.name }), {
        status: 201,
      });
    }),
    // The new Bucket's page, reached after creating it.
    http.get("/api/feeds/:feedId", () =>
      HttpResponse.json(bucket({ id: "bucket-3", name: "Music", title: "Music" })),
    ),
    http.get("/api/feeds/:feedId/items", () =>
      HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
    ),
    http.get("/api/feeds/:feedId/objects", () =>
      HttpResponse.json({ feed_id: "bucket-3", objects: [], total_bytes: 0, count: 0 }),
    ),
  ];
}

describe("Buckets on the Feeds page", () => {
  it("keeps the box collapsed until its header is clicked, then shows one bucket per Feed", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    renderApp("/");

    // The table is up; the box below shows its count and size, nothing more.
    await screen.findByTestId("mirror-row");
    const section = screen.getByRole("region", { name: "Buckets" });
    const header = within(section).getByRole("button", { name: /Buckets/ });
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(header).toHaveTextContent("4 buckets · 12 MB");
    expect(screen.queryAllByTestId("bucket-card")).toHaveLength(0);
    // The actions sit next to the header, outside the toggle.
    expect(within(section).getByRole("button", { name: "New Bucket" })).toBeInTheDocument();

    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    const cards = await screen.findAllByTestId("bucket-card");
    // The Mirrors first, then the Bucket Feeds, then the captured playlists; every card is a
    // Feed's bucket page.
    expect(cards.map((card) => card.textContent)).toEqual([
      expect.stringContaining("Example Podcast"),
      expect.stringContaining("Copycast"),
      expect.stringContaining("Later"),
      expect.stringContaining("Watch Later"),
    ]);
    expect(cards[0]).toHaveAttribute("href", "/buckets/mirror-1");
    expect(cards[3]).toHaveAttribute("href", "/buckets/mirror-wl");
    expect(cards[0]).toHaveTextContent("Synced");
    expect(cards[0]).toHaveTextContent("(3 Episodes)");
    expect(cards[0]).toHaveTextContent("12 MB");
    expect(within(cards[0]!).getByText(/Synced/)).toHaveAttribute("data-sync", "success");
    expect(cards[1]).toHaveTextContent("(2 Episodes)");
    expect(cards[1]).toHaveTextContent("0.0 MB");
    await waitFor(() => expect(cards[1]).toHaveTextContent("Synced"));
    expect(within(cards[1]!).getByRole("time")).toHaveAttribute("datetime", "2024-01-15T09:00:00Z");
    expect(cards[2]).toHaveTextContent("Never synced");
    expect(within(cards[2]!).getByText("Never synced")).toHaveAttribute("data-sync", "warning");
    // No button or dot left of the title any more.
    expect(within(cards[1]!).queryByText("Storage not checked")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(BUCKETS_OPEN_KEY)).toBe("1");

    await user.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryAllByTestId("bucket-card")).toHaveLength(0));
    expect(window.localStorage.getItem(BUCKETS_OPEN_KEY)).toBe("0");
  });

  it("opens for a URL to send, keeps it on the Bucket cards only, and creates a new Bucket", async () => {
    const state = { created: [] as BucketCreate[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    const { history } = renderApp("/?url=https%3A%2F%2Fexample.org%2Fvideo");

    const cards = await screen.findAllByTestId("bucket-card");
    expect(
      screen.getByText("Pick the Bucket that should receive the URL you pasted."),
    ).toBeVisible();
    // Only a Bucket Feed takes a URL: the Mirrors' buckets stay out while one is to be sent.
    const hrefs = cards.map((card) => card.getAttribute("href") ?? "");
    expect(hrefs[0]).toMatch(/^\/buckets\/bucket-1\?url=https(%3A|:)%2F%2Fexample\.org%2Fvideo$/);
    expect(hrefs.every((href) => /^\/buckets\/bucket-\d\?url=/.test(href))).toBe(true);

    await user.click(screen.getByRole("button", { name: "New Bucket" }));
    const dialog = await screen.findByRole("dialog", { name: "New Bucket" });
    expect(within(dialog).getByRole("button", { name: "Create" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Name"), "  Music  ");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(state.created).toEqual([{ name: "Music" }]));
    await waitFor(() => expect(history.location.pathname).toBe("/buckets/bucket-3"));
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
