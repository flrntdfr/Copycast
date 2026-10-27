import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { applyEvent, Invalidator } from "@/api/events";
import type { BucketCreate, BucketUpdate, RequestCreate, RequestRead } from "@/api/types";
import { bucket, item, job, mirror, request } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const copycast = bucket({
  id: "bucket-1",
  name: "Copycast",
  title: "Copycast",
  episode_count: 2,
  request_count: 2,
  storage_bytes: 8_800,
  autoprune_days: 30,
});
const empty = bucket({ id: "bucket-2", name: "Later", title: "Later", request_count: 0 });

function handlers(state: {
  requests: RequestRead[];
  created?: BucketCreate[];
  added?: RequestCreate[];
  updated?: BucketUpdate[];
}) {
  return [
    http.get("/api/feeds", () =>
      HttpResponse.json({
        feeds: [
          copycast,
          empty,
          mirror({ id: "mirror-wl", title: "Watch Later", playlist_capture: true }),
        ],
      }),
    ),
    http.get("/api/youtube/playlists", () =>
      HttpResponse.json({
        playlists: [
          {
            id: "WL",
            title: "Watch Later",
            url: "https://www.youtube.com/playlist?list=WL",
            captured_feed_id: "mirror-wl",
          },
        ],
      }),
    ),
    http.get("/api/feeds/:feedId", ({ params }) =>
      params.feedId === copycast.id
        ? HttpResponse.json(copycast)
        : params.feedId === empty.id
          ? HttpResponse.json(empty)
          : HttpResponse.json(
              { type: "urn:copycast:problem:not-found", status: 404 },
              { status: 404 },
            ),
    ),
    http.get("/api/feeds/:feedId/items", () =>
      HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
    ),
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
    http.post("/api/buckets", async ({ request: req }) => {
      const body = (await req.json()) as BucketCreate;
      state.created?.push(body);
      return HttpResponse.json(bucket({ id: "bucket-3", name: body.name, title: body.name }), {
        status: 201,
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

describe("Buckets on the Feeds page", () => {
  it("shows a card per Bucket with the last Request and creates a new one", async () => {
    const state = {
      requests: [
        request({ status: "expanded", item_count: 3, created_at: "2024-01-15T09:00:00Z" }),
      ],
      created: [] as BucketCreate[],
    };
    server.use(...handlers(state));
    const user = userEvent.setup();
    const { history } = renderApp("/");

    const cards = await screen.findAllByTestId("bucket-card");
    expect(cards).toHaveLength(2);
    // The Buckets sit under the Mirrors, with the captured playlists.
    const section = screen.getByRole("region", { name: "Buckets" });
    expect(within(section).getByTestId("capture-feed-card")).toHaveTextContent("Watch Later");
    expect(cards[0]).toHaveTextContent("Copycast");
    expect(cards[0]).toHaveTextContent("2 Episodes");
    expect(within(cards[0]!).getByText("Storage not checked")).toBeInTheDocument();
    await waitFor(() => expect(cards[0]).toHaveTextContent("Refreshed"));
    expect(cards[1]).toHaveTextContent("Never refreshed");

    await user.click(screen.getByRole("button", { name: "New Bucket" }));
    const dialog = await screen.findByRole("dialog", { name: "New Bucket" });
    expect(within(dialog).getByRole("button", { name: "Create" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Name"), "  Music  ");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(state.created).toEqual([{ name: "Music" }]));
    await waitFor(() => expect(history.location.pathname).toBe("/buckets/bucket-3"));
  });
});

describe("Bucket page", () => {
  it("lists Requests with their produced Episodes and updates on request events", async () => {
    const produced = item({ id: "item-1", feed_id: copycast.id, title: "Produced one" });
    const state = {
      requests: [
        request({
          id: "00000000-0000-4000-8000-00000000aaaa",
          url: "https://www.youtube.com/watch?v=one",
          status: "expanded",
          item_count: 1,
          items: [produced],
          requested_via: "mcp",
        }),
        request({
          id: "00000000-0000-4000-8000-00000000bbbb",
          url: "https://soundcloud.com/set",
          status: "queued",
          job: job({ kind: "expand_request", status: "running" }),
        }),
      ],
      added: [] as RequestCreate[],
    };
    server.use(...handlers(state));
    const user = userEvent.setup();
    const { client } = renderApp(`/buckets/${copycast.id}?tab=requests`);

    expect(await screen.findByRole("heading", { name: "Copycast" })).toBeInTheDocument();
    expect(screen.getByText(/2 Episodes · 2 Requests · 8\.8 kB/)).toBeInTheDocument();
    expect(screen.getByLabelText("Bucket Feed URL")).toHaveValue(copycast.feed_url);

    const rows = await screen.findAllByTestId("request-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("https://www.youtube.com/watch?v=one");
    expect(rows[0]).toHaveTextContent("MCP");
    expect(rows[0]).toHaveTextContent("Expanded · 1 Episode");
    expect(rows[1]).toHaveTextContent("Expand Request · Running");

    await user.click(within(rows[0]!).getByRole("button", { name: "Show produced Episodes" }));
    const list = await screen.findByRole("list", { name: "Produced Episodes" });
    // `tab=episodes` is the default and is stripped from the URL.
    expect(within(list).getByRole("link", { name: "Produced one" })).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/buckets\/bucket-1\?q=Produced(\+|%20)one$/),
    );
    expect(within(list).getByText("Listed")).toBeInTheDocument();

    // Add a URL from the header form; the new Request shows up first.
    await user.type(screen.getByLabelText("Add URL"), "https://example.org/video");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(state.added).toEqual([{ url: "https://example.org/video" }]));
    expect(await screen.findByText("Request queued")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("request-row")).toHaveLength(3));
    expect(screen.getByLabelText("Add URL")).toHaveValue("");

    // A `request` event from the worker refetches the table (expanded now).
    state.requests = state.requests.map((r) =>
      r.status === "queued" ? { ...r, status: "expanded", item_count: 4, job: null } : r,
    );
    applyEvent(
      {
        event: "request",
        data: {
          feed_id: copycast.id,
          request_id: "00000000-0000-4000-8000-00000000bbbb",
          status: "expanded",
          item_count: 4,
        },
      },
      new Invalidator(client, 0),
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("request-row")[2]).toHaveTextContent("Expanded · 4 Episodes"),
    );
  });

  it("saves renamed settings and autoprune, and opens the prune dialog from the header", async () => {
    const state = { requests: [] as RequestRead[], updated: [] as BucketUpdate[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/buckets/${copycast.id}?tab=settings`);

    const form = await screen.findByRole("form", { name: "Bucket settings" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Everything");
    await user.click(within(form).getByRole("switch", { name: "Autoprune" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(state.updated).toEqual([{ name: "Everything", autoprune_days: null }]),
    );
    expect(await screen.findByText("Bucket settings saved")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Prune now…" }));
    const dialog = await screen.findByRole("dialog", { name: "Prune this Bucket" });
    await waitFor(() =>
      expect(within(dialog).getByTestId("prune-preview")).toHaveTextContent(
        "Deletes 1 Episode (4.4 kB)",
      ),
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Delete Bucket…" })).toBeInTheDocument();
  });

  it("shows the empty Requests state and a 404 for an unknown Bucket", async () => {
    server.use(...handlers({ requests: [] }));
    renderApp(`/buckets/${empty.id}?tab=requests`);
    expect(await screen.findByText("No Requests yet")).toBeInTheDocument();
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
