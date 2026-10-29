import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { BucketRead, BucketUpdate, RequestCreate } from "@/api/types";
import { bucket, item } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const copycast = bucket({
  id: "bucket-1",
  name: "Copycast",
  title: "Copycast",
  episode_count: 2,
  request_count: 2,
  storage_bytes: 12_345_678,
  autoprune_days: 30,
  is_default: true,
});
const later = bucket({ id: "bucket-2", name: "Later", title: "Later" });

interface Calls {
  requests: RequestCreate[];
  patches: BucketUpdate[];
}

function handlers(calls: Calls = { requests: [], patches: [] }, feeds: BucketRead[] = []) {
  const all = [copycast, later, ...feeds];
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds: all })),
    http.get("/api/feeds/:feedId", ({ params }) => {
      const feed = all.find((candidate) => candidate.id === params.feedId);
      return feed
        ? HttpResponse.json(feed)
        : HttpResponse.json(
            { type: "urn:copycast:problem:not-found", status: 404 },
            { status: 404, headers: { "Content-Type": "application/problem+json" } },
          );
    }),
    http.get("/api/feeds/:feedId/items", ({ params }) =>
      HttpResponse.json({
        items: [item({ id: "item-1", feed_id: String(params.feedId), title: "A pushed video" })],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    ),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 100, offset: 0 })),
    http.post("/api/buckets/:bucketId/requests", async ({ params, request }) => {
      const body = (await request.json()) as RequestCreate;
      calls.requests.push(body);
      return HttpResponse.json(
        {
          id: "request-1",
          bucket_id: String(params.bucketId),
          url: body.url,
          status: "queued",
          requested_via: "ui",
          created_at: "2024-01-15T09:00:00Z",
          expanded_at: null,
          item_ids: [],
          error: null,
        },
        { status: 202 },
      );
    }),
    http.patch("/api/buckets/:bucketId", async ({ params, request }) => {
      const body = (await request.json()) as BucketUpdate;
      calls.patches.push(body);
      const feed = all.find((candidate) => candidate.id === params.bucketId) ?? copycast;
      return HttpResponse.json({ ...feed, ...body });
    }),
  ];
}

describe("Local feed page", () => {
  it("shows the header, the feed URL, the Add URL form and the Catalog", async () => {
    server.use(...handlers());
    renderApp("/feed/bucket-1");

    expect(await screen.findByRole("heading", { name: "Copycast" })).toBeInTheDocument();
    expect(screen.getByText("Local")).toBeInTheDocument();
    expect(
      screen.getByText("2 Episodes · 12 MB · Autoprune 30 days after the first download"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Feed URL")).toHaveValue(copycast.feed_url);
    expect(screen.getByRole("button", { name: "Upload files…" })).toBeInTheDocument();
    expect(screen.getByLabelText("Audio files to upload")).toHaveAttribute("type", "file");
    expect(screen.getByRole("link", { name: "Files" })).toHaveAttribute(
      "href",
      "/buckets/bucket-1",
    );
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Add URL" })).toHaveValue("");
    expect(await screen.findByText("A pushed video")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Filter by title" })).toBeInTheDocument();
    // The Mirror-only bits stay away: no Refresh, no Pause, no "#" column.
    expect(screen.queryByRole("button", { name: "Refresh now" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "#" })).not.toBeInTheDocument();
  });

  it("says nothing about autoprune when it is off", async () => {
    server.use(...handlers());
    renderApp("/feed/bucket-2");
    await screen.findByRole("heading", { name: "Later" });
    expect(screen.getByText("0 Episodes · 0.0 MB")).toBeInTheDocument();
    expect(screen.queryByText(/Autoprune/)).not.toBeInTheDocument();
  });

  it("prefills Add URL from ?url=, cleans the URL and queues the Request", async () => {
    const calls: Calls = { requests: [], patches: [] };
    server.use(...handlers(calls));
    const user = userEvent.setup();
    const { history } = renderApp(
      `/feed/bucket-1?url=${encodeURIComponent("https://youtu.be/abc")}`,
    );
    const field = await screen.findByRole("textbox", { name: "Add URL" });
    expect(field).toHaveValue("https://youtu.be/abc");
    await waitFor(() => expect(history.location.search).toBe(""));
    expect(history.location.pathname).toBe("/feed/bucket-1");

    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(calls.requests).toEqual([{ url: "https://youtu.be/abc" }]));
    expect(await screen.findByText("Request queued")).toBeInTheDocument();
    expect(field).toHaveValue("");
  });

  it("edits the name and autoprune in the Settings dialog, with no Prune now", async () => {
    const calls: Calls = { requests: [], patches: [] };
    server.use(...handlers(calls));
    const user = userEvent.setup();
    renderApp("/feed/bucket-1");
    await screen.findByRole("heading", { name: "Copycast" });
    await user.click(screen.getByRole("button", { name: "Settings" }));

    const dialog = await screen.findByRole("dialog", { name: "Local feed settings" });
    const form = within(dialog).getByRole("form", { name: "Local feed settings" });
    expect(within(form).getByLabelText("Name")).toHaveValue("Copycast");
    expect(within(form).getByRole("switch", { name: "Autoprune" })).toBeChecked();
    expect(within(form).getByLabelText("Days after first download")).toHaveValue(30);
    expect(within(dialog).queryByRole("button", { name: /Prune now/ })).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/Bucket/);

    // The default Local feed can be renamed but not deleted.
    expect(within(dialog).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("never deleted");

    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Inbox");
    await user.click(within(form).getByRole("switch", { name: "Autoprune" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.patches).toEqual([{ name: "Inbox", autoprune_days: null }]));
    expect(await screen.findByText("Local feed settings saved")).toBeInTheDocument();
  });

  it("uploads the picked files in natural name order and adds them to the Local feed", async () => {
    const staged: string[] = [];
    const put: { id: string; body: string }[] = [];
    let added: unknown = null;
    server.use(
      ...handlers(),
      http.post("/api/uploads", async ({ request }) => {
        const { filename } = (await request.json()) as { filename: string };
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
      http.put("/uploads/:id/:token", async ({ params, request }) => {
        put.push({ id: String(params.id), body: await request.text() });
        return HttpResponse.json({ id: params.id, state: "received" });
      }),
      http.post("/api/buckets/:bucketId/uploads", async ({ request }) => {
        added = await request.json();
        return HttpResponse.json(copycast);
      }),
    );
    const user = userEvent.setup();
    renderApp("/feed/bucket-1");
    await screen.findByRole("heading", { name: "Copycast" });
    const files = ["Part 10.mp3", "part 2.mp3", "Part 1.mp3"].map(
      (name) => new File([`ID3 ${name}`], name, { type: "audio/mpeg" }),
    );
    await user.upload(screen.getByLabelText("Audio files to upload"), files);

    const dialog = await screen.findByRole("dialog", { name: "Upload to Copycast" });
    await waitFor(() => expect(added).toEqual({ upload_ids: ["u1", "u2", "u3"] }));
    expect(staged).toEqual(["Part 1.mp3", "part 2.mp3", "Part 10.mp3"]);
    // Each PUT carries the file's own bytes.
    expect(put).toEqual([
      { id: "u1", body: "ID3 Part 1.mp3" },
      { id: "u2", body: "ID3 part 2.mp3" },
      { id: "u3", body: "ID3 Part 10.mp3" },
    ]);
    expect(await within(dialog).findByText("Done")).toBeInTheDocument();
  });

  it("offers Delete for a Local feed that is not the default", async () => {
    server.use(...handlers());
    const user = userEvent.setup();
    renderApp("/feed/bucket-2");
    await screen.findByRole("heading", { name: "Later" });
    await user.click(screen.getByRole("button", { name: "Settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Local feed settings" });
    await user.click(within(dialog).getByRole("button", { name: "Delete Local feed…" }));
    const confirm = await screen.findByRole("alertdialog");
    expect(confirm).toHaveTextContent("Delete Local feed “Later”?");
  });
});
