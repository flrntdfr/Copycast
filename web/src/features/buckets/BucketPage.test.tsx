import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { BucketUpdate, FeedObjectRead, RequestCreate, RequestRead } from "@/api/types";
import { bucket, feedObject, mirror, mirrorDefaults, request } from "@/test/factories";
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
const empty = bucket({ id: "bucket-2", name: "Later", title: "Later", request_count: 0 });
const podcast = mirror({ id: "mirror-1", title: "Example Podcast" });

const objects: FeedObjectRead[] = [
  feedObject({
    name: "0123456789abcdef.m4a",
    path: "media/0123456789abcdef.m4a",
    size_bytes: 4_407,
    item_title: "Episode one",
  }),
  feedObject({
    name: "feed.artwork.jpg",
    path: "assets/feed.artwork.jpg",
    kind: "artwork",
    size_bytes: 1_200,
    item_id: null,
    item_title: null,
  }),
  feedObject({
    name: "feed.json",
    path: "feed.json",
    kind: "descriptor",
    size_bytes: 300,
    item_id: null,
    item_title: null,
  }),
];

function handlers(state: {
  requests: RequestRead[];
  objects?: FeedObjectRead[];
  added?: RequestCreate[];
  updated?: BucketUpdate[];
}) {
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
    http.get("/api/settings/defaults", () => HttpResponse.json(mirrorDefaults())),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.get("/api/buckets/:bucketId/requests", ({ request: req }) => {
      const limit = Number(new URL(req.url).searchParams.get("limit") ?? 100);
      return HttpResponse.json({
        requests: state.requests.slice(0, limit),
        total: state.requests.length,
        limit,
        offset: 0,
      });
    }),
    http.post("/api/buckets/:bucketId/requests", async ({ request: req }) => {
      const body = (await req.json()) as RequestCreate;
      state.added?.push(body);
      const created = request({ url: body.url, bucket_id: copycast.id });
      state.requests = [created, ...state.requests];
      return HttpResponse.json(created, { status: 202 });
    }),
    http.patch("/api/buckets/:bucketId", async ({ request: req }) => {
      const body = (await req.json()) as BucketUpdate;
      state.updated?.push(body);
      return HttpResponse.json({
        ...copycast,
        name: body.name ?? copycast.name,
        autoprune_days: "autoprune_days" in body ? body.autoprune_days : copycast.autoprune_days,
      });
    }),
    http.post("/api/buckets/:bucketId/prune", () =>
      HttpResponse.json({ matched: 1, deleted_count: 0, bytes_freed: 4_407, dry_run: true }),
    ),
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
  it("shows the storage view of a Bucket: every stored file, downloadable, and the Add URL form", async () => {
    const state = {
      requests: [request({ status: "expanded", created_at: "2024-01-15T09:00:00Z" })],
      added: [] as RequestCreate[],
    };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);

    expect(await screen.findByRole("heading", { name: "Copycast" })).toBeInTheDocument();
    expect(screen.getByText("Local")).toBeInTheDocument();
    expect(screen.getByText(/2 Episodes · 0\.0 MB · Autoprune 30 days/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/^Synced/)).toBeInTheDocument());
    // No feed URL, no credentials, no tabs, no Catalog filter on the storage view.
    expect(screen.queryByRole("textbox", { name: "Bucket Feed URL" })).not.toBeInTheDocument();
    expect(screen.queryByText("Username and password")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prune now…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "More actions" })).not.toBeInTheDocument();

    const rows = await screen.findAllByTestId("object-row");
    await waitFor(() => expect(screen.getAllByTestId("object-row")).toHaveLength(4));
    // The published feed comes first, downloadable without the Basic pair.
    expect(rows[0]).toHaveTextContent("bucket-1.xml");
    expect(rows[0]).toHaveTextContent("Feed");
    expect(within(rows[0]!).getByRole("link", { name: "Download bucket-1.xml" })).toHaveAttribute(
      "href",
      "http://localhost:8080/feeds/bucket-1.xml",
    );
    const all = screen.getAllByTestId("object-row");
    expect(all[1]).toHaveTextContent("0123456789abcdef.m4a");
    expect(all[1]).toHaveTextContent("Episode one");
    expect(all[1]).toHaveTextContent("Media");
    expect(all[1]).toHaveTextContent("4.4 kB");
    expect(
      within(all[1]!).getByRole("link", { name: "Download 0123456789abcdef.m4a" }),
    ).toHaveAttribute("href", objects[0]!.url);
    expect(within(all[1]!).getByRole("link", { name: /Download/ })).toHaveAttribute(
      "download",
      "0123456789abcdef.m4a",
    );
    expect(all[2]).toHaveTextContent("Artwork");
    expect(all[3]).toHaveTextContent("Descriptor");
    expect(screen.getByTestId("objects-summary")).toHaveTextContent("3 files · 0.0 MB");

    // The Add URL form stays: it is how URLs get into a Bucket.
    await user.type(screen.getByLabelText("Add URL"), "https://example.org/video");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(state.added).toEqual([{ url: "https://example.org/video" }]));
    expect(await screen.findByText("Request queued")).toBeInTheDocument();
    expect(screen.getByLabelText("Add URL")).toHaveValue("");
  });

  it("opens Settings: saves the renamed Bucket and autoprune, previews a prune, keeps Delete", async () => {
    const state = { requests: [] as RequestRead[], updated: [] as BucketUpdate[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);

    await screen.findByRole("heading", { name: "Copycast" });
    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Bucket settings" });
    const form = within(dialog).getByRole("form", { name: "Bucket settings" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Everything");
    await user.click(within(form).getByRole("switch", { name: "Autoprune" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(state.updated).toEqual([{ name: "Everything", autoprune_days: null }]),
    );
    expect(await screen.findByText("Bucket settings saved")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Prune now…" }));
    const prune = await screen.findByRole("dialog", { name: "Prune this Bucket" });
    await waitFor(() =>
      expect(within(prune).getByTestId("prune-preview")).toHaveTextContent(
        "Deletes 1 Episode (4.4 kB)",
      ),
    );
    await user.click(within(prune).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Prune this Bucket" })).not.toBeInTheDocument(),
    );
    expect(within(dialog).getByRole("button", { name: "Delete Bucket…" })).toBeInTheDocument();
  });

  it("renders a Mirror's storage view with Open Mirror and the Mirror settings dialog", async () => {
    server.use(...handlers({ requests: [] }));
    const user = userEvent.setup();
    renderApp(`/buckets/${podcast.id}`);

    expect(await screen.findByRole("heading", { name: "Example Podcast" })).toBeInTheDocument();
    expect(screen.getByText("RSS")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add URL")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Feed URL/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Mirror" })).toHaveAttribute(
      "href",
      "/mirrors/mirror-1",
    );
    await waitFor(() => expect(screen.getAllByTestId("object-row")).toHaveLength(4));
    expect(screen.getAllByTestId("object-row")[0]).toHaveTextContent("mirror-1.xml");

    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Mirror settings" });
    expect(within(dialog).getByText("Example Podcast")).toBeInTheDocument();
    expect(await within(dialog).findByLabelText("Source URL")).toHaveValue(podcast.source_url);
  });

  it("shows the empty state for a Bucket without files and a 404 for an unknown Feed", async () => {
    server.use(...handlers({ requests: [] }));
    renderApp(`/buckets/${empty.id}`);
    expect(await screen.findByText("Nothing stored yet")).toBeInTheDocument();
    expect(screen.getByText("Never synced")).toBeInTheDocument();

    server.use(...handlers({ requests: [] }));
    renderApp("/buckets/nope");
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
  });
});

describe("Upload files", () => {
  it("uploads the picked files in natural name order and adds them to the Bucket", async () => {
    const staged: string[] = [];
    const put: string[] = [];
    let added: unknown = null;
    server.use(
      ...handlers({ requests: [] }),
      http.post("/api/uploads", async ({ request: req }) => {
        const { filename } = (await req.json()) as { filename: string };
        staged.push(filename);
        const id = `u${staged.length}`;
        return HttpResponse.json(
          {
            id,
            filename,
            state: "pending",
            upload_url: `http://public.example/uploads/${id}/token`,
            size_bytes: null,
            tags: null,
            created_at: "2026-10-27T00:00:00Z",
          },
          { status: 201 },
        );
      }),
      http.put("/uploads/:id/:token", ({ params }) => {
        put.push(String(params.id));
        return HttpResponse.json({ id: params.id, state: "received" });
      }),
      http.post("/api/buckets/:bucketId/uploads", async ({ request: req }) => {
        added = await req.json();
        return HttpResponse.json(copycast);
      }),
    );
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}`);
    await screen.findByRole("heading", { name: "Copycast" });
    const files = ["Part 10.mp3", "part 2.mp3", "Part 1.mp3"].map(
      (name) => new File(["ID3"], name, { type: "audio/mpeg" }),
    );
    await user.upload(screen.getByLabelText("Audio files to upload"), files);

    const dialog = await screen.findByRole("dialog", { name: "Upload to Copycast" });
    await waitFor(() => expect(added).toEqual({ upload_ids: ["u1", "u2", "u3"] }));
    expect(staged).toEqual(["Part 1.mp3", "part 2.mp3", "Part 10.mp3"]);
    expect(put).toEqual(["u1", "u2", "u3"]);
    expect(await within(dialog).findByText("Done")).toBeInTheDocument();
  });
});
