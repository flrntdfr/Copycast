import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ClearFeedResult, FeedObjectRead, Schemas } from "@/api/types";
import { bucket, feedObject, item, mirror } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";
import { bareFeedUrl } from "./FeedObjectsTable";

const copycast = bucket({
  id: "bucket-1",
  name: "Copycast",
  title: "Copycast",
  feed_url: "http://k7mpq2xz:s3cret@localhost:8080/feeds/bucket-1.xml",
  episode_count: 2,
  request_count: 2,
  storage_bytes: 8_800,
  autoprune_days: 30,
});
type ArchiveFlagUpdate = Schemas["ArchiveFlagUpdate"];
type ClearFeedRequest = Schemas["ClearFeedRequest"];

const empty = bucket({ id: "bucket-2", name: "Later", title: "Later", request_count: 0 });
const podcast = mirror({ id: "mirror-1", title: "Example Podcast" });

const objects: FeedObjectRead[] = [
  feedObject({
    name: "0123456789abcdef.m4a",
    path: "media/0123456789abcdef.m4a",
    size_bytes: 4_407,
    item_title: "Episode one",
    locked: false,
  }),
  feedObject({
    name: "fedcba9876543210.m4a",
    path: "media/fedcba9876543210.m4a",
    size_bytes: 2_000_000,
    item_id: "fedcba9876543210",
    item_title: "Episode two",
    locked: true,
  }),
  feedObject({
    name: "feed.artwork.jpg",
    path: "assets/feed.artwork.jpg",
    kind: "artwork",
    size_bytes: 1_200,
    item_id: null,
    item_title: null,
    locked: null,
  }),
  feedObject({
    name: "feed.json",
    path: "feed.json",
    kind: "descriptor",
    size_bytes: 300,
    item_id: null,
    item_title: null,
    locked: null,
  }),
];

function handlers(
  state: {
    objects?: FeedObjectRead[];
    preview?: Partial<ClearFeedResult>;
    cleared?: ClearFeedRequest[];
    locks?: { itemId: string; body: ArchiveFlagUpdate }[];
  } = {},
) {
  const feeds = [copycast, empty, podcast];
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds })),
    http.get("/api/feeds/:feedId", ({ params }) => {
      const feed = feeds.find((candidate) => candidate.id === params.feedId);
      return feed
        ? HttpResponse.json(feed)
        : HttpResponse.json(
            { type: "urn:copycast:problem:not-found", status: 404 },
            { status: 404 },
          );
    }),
    http.get("/api/feeds/:feedId/objects", ({ params }) => {
      const listed = params.feedId === empty.id ? [] : (state.objects ?? objects);
      return HttpResponse.json({
        feed_id: params.feedId,
        objects: listed,
        total_bytes: listed.reduce((sum, object) => sum + object.size_bytes, 0),
        count: listed.length,
      });
    }),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.post("/api/feeds/:feedId/clear", async ({ request: req }) => {
      const body = (await req.json()) as ClearFeedRequest;
      const preview: ClearFeedResult = {
        matched: 1,
        deleted_count: 0,
        bytes_freed: 4_407,
        dry_run: true,
        kept_count: 1,
        kept_bytes: 2_000_000,
        ...state.preview,
      };
      if (body.dry_run) return HttpResponse.json(preview);
      state.cleared?.push(body);
      return HttpResponse.json({ ...preview, deleted_count: preview.matched, dry_run: false });
    }),
    http.put("/api/feeds/:feedId/items/:itemId/archive-flag", async ({ params, request: req }) => {
      const body = (await req.json()) as ArchiveFlagUpdate;
      state.locks?.push({ itemId: String(params.itemId), body });
      return HttpResponse.json(
        item({ id: String(params.itemId), feed_id: copycast.id, archive_flag: body.archive_flag }),
      );
    }),
  ];
}

describe("bareFeedUrl", () => {
  it("drops the Basic pair and leaves a URL without one alone", () => {
    expect(bareFeedUrl("http://u:p@localhost:8080/feeds/a.xml")).toBe(
      "http://localhost:8080/feeds/a.xml",
    );
    expect(bareFeedUrl("http://localhost:8080/feeds/a.xml")).toBe(
      "http://localhost:8080/feeds/a.xml",
    );
    expect(bareFeedUrl("not a url")).toBe("not a url");
  });
});

describe("Bucket page", () => {
  it("shows a Local feed's bucket: files and size, Open feed and Purge, every file downloadable", async () => {
    server.use(...handlers());
    renderApp(`/buckets/${copycast.id}`);

    expect(await screen.findByRole("heading", { name: "Copycast" })).toBeInTheDocument();
    expect(screen.getByText("Local")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("bucket-summary")).toHaveTextContent("4 files · 2.0 MB"),
    );
    expect(screen.getByRole("link", { name: "Open feed" })).toHaveAttribute(
      "href",
      "/feed/bucket-1",
    );
    expect(screen.getByRole("button", { name: "Purge…" })).toBeInTheDocument();
    // Every setting lives on the feed page now: no sync badge, Settings, Upload or Add URL here.
    expect(screen.queryByText(/^Synced|Never synced/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Add URL")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Audio files to upload")).not.toBeInTheDocument();
    expect(screen.queryByTestId("objects-summary")).not.toBeInTheDocument();

    await waitFor(() => expect(screen.getAllByTestId("object-row")).toHaveLength(5));
    const rows = screen.getAllByTestId("object-row");
    // The published feed comes first, downloadable without the Basic pair, never purged.
    expect(rows[0]).toHaveTextContent("bucket-1.xml");
    expect(rows[0]).toHaveTextContent("Feed");
    expect(within(rows[0]!).getByRole("link", { name: "Download bucket-1.xml" })).toHaveAttribute(
      "href",
      "http://localhost:8080/feeds/bucket-1.xml",
    );
    expect(
      within(rows[0]!).getByRole("button", { name: "Part of the feed itself: never purged" }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(rows[1]).toHaveTextContent("0123456789abcdef.m4a");
    expect(rows[1]).toHaveTextContent("Episode one");
    expect(rows[1]).toHaveTextContent("Media");
    expect(rows[1]).toHaveTextContent("4.4 kB");
    const download = within(rows[1]!).getByRole("link", {
      name: "Download 0123456789abcdef.m4a",
    });
    expect(download).toHaveAttribute("href", objects[0]!.url);
    expect(download).toHaveAttribute("download", "0123456789abcdef.m4a");
    expect(rows[3]).toHaveTextContent("Artwork");
    expect(rows[4]).toHaveTextContent("Descriptor");
  });

  it("locks and unlocks the Episode a file belongs to; the feed's own files have no lock", async () => {
    const state = { locks: [] as { itemId: string; body: ArchiveFlagUpdate }[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);

    await waitFor(() => expect(screen.getAllByTestId("object-row")).toHaveLength(5));
    const rows = screen.getAllByTestId("object-row");
    const lock = within(rows[1]!).getByRole("button", { name: "Lock 0123456789abcdef.m4a" });
    expect(lock).toHaveAttribute("aria-pressed", "false");
    const unlock = within(rows[2]!).getByRole("button", { name: "Lock fedcba9876543210.m4a" });
    expect(unlock).toHaveAttribute("aria-pressed", "true");
    for (const row of [rows[3]!, rows[4]!]) {
      expect(
        within(row).getByRole("button", { name: "Part of the feed itself: never purged" }),
      ).toHaveAttribute("aria-disabled", "true");
    }

    await user.click(lock);
    expect(await screen.findByText("Locked")).toBeInTheDocument();
    await user.click(unlock);
    expect(await screen.findByText("Unlocked")).toBeInTheDocument();
    expect(state.locks).toEqual([
      { itemId: "0123456789abcdef", body: { archive_flag: true } },
      { itemId: "fedcba9876543210", body: { archive_flag: false } },
    ]);
  });

  it("purges every Episode that is not locked after warning about the locked files", async () => {
    const state = { cleared: [] as ClearFeedRequest[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);

    await user.click(await screen.findByRole("button", { name: "Purge…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Purge “Copycast”?" });
    const summary = within(dialog).getByTestId("purge-summary");
    await waitFor(() =>
      expect(summary).toHaveTextContent("Purging deletes 1 Episode (0.0 MB) that is not locked."),
    );
    expect(summary).toHaveTextContent("1 file is locked and will not be deleted.");
    expect(summary).toHaveTextContent("Delete the others?");

    await user.click(within(dialog).getByRole("button", { name: "Yes, purge" }));
    await waitFor(() => expect(state.cleared).toEqual([{ dry_run: false }]));
    expect(await screen.findByText("1 Episode deleted")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("disables the purge when every Episode is locked, and No closes the dialog", async () => {
    const state = { preview: { matched: 0, bytes_freed: 0 }, cleared: [] as ClearFeedRequest[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);

    await user.click(await screen.findByRole("button", { name: "Purge…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(
      await within(dialog).findByText("Nothing to purge: every Episode is locked."),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Yes, purge" })).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "No" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(state.cleared).toEqual([]);
  });

  it("says there is nothing to purge in a bucket without locks or Episodes", async () => {
    server.use(
      ...handlers({ objects: [], preview: { matched: 0, bytes_freed: 0, kept_count: 0 } }),
    );
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);

    await user.click(await screen.findByRole("button", { name: "Purge…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(await within(dialog).findByText("Nothing to purge.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Yes, purge" })).toBeDisabled();
  });

  it("shows a Mirror's bucket with its service and a link to its feed page", async () => {
    server.use(...handlers());
    renderApp(`/buckets/${podcast.id}`);

    expect(await screen.findByRole("heading", { name: "Example Podcast" })).toBeInTheDocument();
    expect(screen.getByText("RSS")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open feed" })).toHaveAttribute(
      "href",
      "/feed/mirror-1",
    );
    await waitFor(() => expect(screen.getAllByTestId("object-row")).toHaveLength(5));
    expect(screen.getAllByTestId("object-row")[0]).toHaveTextContent("mirror-1.xml");
  });

  it("shows the empty state for a bucket without files and a 404 for an unknown Feed", async () => {
    server.use(...handlers());
    renderApp(`/buckets/${empty.id}`);
    expect(await screen.findByText("Nothing stored yet")).toBeInTheDocument();
    expect(screen.getByTestId("bucket-summary")).toHaveTextContent("0 files · 0.0 MB");

    server.use(...handlers());
    renderApp("/buckets/nope");
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
  });
});
