import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { applyEvent, Invalidator } from "@/api/events";
import type { InboxCreate, InboxUpdate, RequestCreate, RequestRead } from "@/api/types";
import { inbox, item, job, request } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const copycast = inbox({
  id: "inbox-1",
  name: "Copycast",
  title: "Copycast",
  episode_count: 2,
  request_count: 2,
  storage_bytes: 8_800,
  autoprune_days: 30,
});
const empty = inbox({ id: "inbox-2", name: "Later", title: "Later", request_count: 0 });

function handlers(state: {
  requests: RequestRead[];
  created?: InboxCreate[];
  added?: RequestCreate[];
  updated?: InboxUpdate[];
}) {
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [copycast, empty] })),
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
    http.get("/api/inboxes/:inboxId/requests", ({ request: req }) => {
      const limit = Number(new URL(req.url).searchParams.get("limit") ?? 100);
      return HttpResponse.json({
        requests: state.requests.slice(0, limit),
        total: state.requests.length,
        limit,
        offset: 0,
      });
    }),
    http.post("/api/inboxes", async ({ request: req }) => {
      const body = (await req.json()) as InboxCreate;
      state.created?.push(body);
      return HttpResponse.json(inbox({ id: "inbox-3", name: body.name, title: body.name }), {
        status: 201,
      });
    }),
    http.post("/api/inboxes/:inboxId/requests", async ({ request: req }) => {
      const body = (await req.json()) as RequestCreate;
      state.added?.push(body);
      const created = request({ url: body.url, inbox_id: copycast.id });
      state.requests = [created, ...state.requests];
      return HttpResponse.json(created, { status: 202 });
    }),
    http.patch("/api/inboxes/:inboxId", async ({ request: req }) => {
      const body = (await req.json()) as InboxUpdate;
      state.updated?.push(body);
      return HttpResponse.json({
        ...copycast,
        name: body.name ?? copycast.name,
        autoprune_days: "autoprune_days" in body ? body.autoprune_days : copycast.autoprune_days,
      });
    }),
    http.post("/api/inboxes/:inboxId/prune", () =>
      HttpResponse.json({ matched: 1, deleted_count: 0, bytes_freed: 4_407, dry_run: true }),
    ),
  ];
}

describe("Inboxes list", () => {
  it("shows a card per Inbox with the last Request and creates a new one", async () => {
    const state = {
      requests: [
        request({ status: "expanded", item_count: 3, created_at: "2024-01-15T09:00:00Z" }),
      ],
      created: [] as InboxCreate[],
    };
    server.use(...handlers(state));
    const user = userEvent.setup();
    const { history } = renderApp("/inboxes");

    const cards = await screen.findAllByTestId("inbox-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Copycast");
    expect(cards[0]).toHaveTextContent("2 Episodes · 8.8 kB · 2 Requests");
    expect(cards[0]).toHaveTextContent("Autoprune 30 days after the first download");
    await waitFor(() => expect(cards[0]).toHaveTextContent("Last Request"));
    expect(cards[0]).toHaveTextContent("Expanded");
    expect(cards[1]).toHaveTextContent("No Requests yet");
    expect(cards[1]).toHaveTextContent("No autoprune");

    await user.click(screen.getByRole("button", { name: "New Inbox" }));
    const dialog = await screen.findByRole("dialog", { name: "New Inbox" });
    expect(within(dialog).getByRole("button", { name: "Create" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Name"), "  Music  ");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(state.created).toEqual([{ name: "Music" }]));
    await waitFor(() => expect(history.location.pathname).toBe("/inboxes/inbox-3"));
  });
});

describe("Inbox page", () => {
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
    const { client } = renderApp(`/inboxes/${copycast.id}?tab=requests`);

    expect(await screen.findByRole("heading", { name: "Copycast" })).toBeInTheDocument();
    expect(screen.getByText(/2 Episodes · 2 Requests · 8\.8 kB/)).toBeInTheDocument();
    expect(screen.getByLabelText("Inbox Feed URL")).toHaveValue(copycast.feed_url);

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
      expect.stringMatching(/^\/inboxes\/inbox-1\?q=Produced(\+|%20)one$/),
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
    const state = { requests: [] as RequestRead[], updated: [] as InboxUpdate[] };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp(`/inboxes/${copycast.id}?tab=settings`);

    const form = await screen.findByRole("form", { name: "Inbox settings" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Everything");
    await user.click(within(form).getByRole("switch", { name: "Autoprune" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(state.updated).toEqual([{ name: "Everything", autoprune_days: null }]),
    );
    expect(await screen.findByText("Inbox settings saved")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Prune now…" }));
    const dialog = await screen.findByRole("dialog", { name: "Prune this Inbox" });
    await waitFor(() =>
      expect(within(dialog).getByTestId("prune-preview")).toHaveTextContent(
        "Deletes 1 Episode (4.4 kB)",
      ),
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Delete Inbox…" })).toBeInTheDocument();
  });

  it("shows the empty Requests state and a 404 for an unknown Inbox", async () => {
    server.use(...handlers({ requests: [] }));
    renderApp(`/inboxes/${empty.id}?tab=requests`);
    expect(await screen.findByText("No Requests yet")).toBeInTheDocument();
  });
});
