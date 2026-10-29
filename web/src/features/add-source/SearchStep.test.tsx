import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { RequestCreate } from "@/api/types";
import { bucket, request, videoResult } from "@/test/factories";
import { openAddSource } from "@/stores/addSource";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

describe("Search step", () => {
  it("lists YouTube hits next to podcasts and sends one to the Local feed", async () => {
    const sent: RequestCreate[] = [];
    const copycast = bucket({ id: "bucket-1", name: "Copycast", title: "Copycast" });
    server.use(
      http.get("/api/feeds", () => HttpResponse.json({ feeds: [copycast] })),
      http.get("/api/feeds/:feedId", () => HttpResponse.json(copycast)),
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
      ),
      http.get("/api/buckets/:bucketId/requests", () =>
        HttpResponse.json({ requests: [], total: 0, limit: 100, offset: 0 }),
      ),
      http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
      http.get("/api/search/podcasts", () =>
        HttpResponse.json({ query: "gruber wwdc", results: [] }),
      ),
      http.get("/api/search/videos", ({ request: req }) => {
        const query = new URL(req.url).searchParams.get("query");
        return HttpResponse.json({ query, results: [videoResult()] });
      }),
      http.post("/api/buckets/:bucketId/requests", async ({ request: req }) => {
        const body = (await req.json()) as RequestCreate;
        sent.push(body);
        return HttpResponse.json(request({ url: body.url, bucket_id: "bucket-1" }), {
          status: 201,
        });
      }),
    );
    const user = userEvent.setup();
    const { history } = renderApp("/");
    await screen.findByRole("heading", { name: "Feeds", level: 1 });
    openAddSource({ query: "gruber wwdc" });

    const videos = await screen.findByRole("list", { name: "Video results" });
    const row = await within(videos).findByRole("listitem");
    expect(row).toHaveTextContent("WWDC 2024 Live from Cupertino");
    expect(row).toHaveTextContent("The Talk Show");
    expect(row).toHaveTextContent("1:30:00");
    expect(screen.getByText("No podcasts found")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Send to Local feed" }));
    await waitFor(() => expect(sent).toEqual([{ url: "https://www.youtube.com/watch?v=abc123" }]));
    await waitFor(() => expect(history.location.pathname).toBe("/feed/bucket-1"));
  });
});
