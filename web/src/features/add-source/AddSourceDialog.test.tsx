import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { MirrorCreate } from "@/api/types";
import {
  candidate,
  inbox,
  job,
  mirror,
  mirrorDefaults,
  probeResult,
  problem,
} from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const feedUrl = "https://podcast.example/feed.xml";
const bonusUrl = "https://bonus.example/feed.xml";

function baseHandlers(created: MirrorCreate[] = []) {
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [inbox({ id: "inbox-1" })] })),
    http.get("/api/settings/defaults", () => HttpResponse.json(mirrorDefaults())),
    http.get("/api/jobs", () =>
      HttpResponse.json({
        jobs: [job({ status: "running", feed_id: "mirror-new" })],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    ),
    http.get("/api/feeds/:feedId", () =>
      HttpResponse.json(mirror({ id: "mirror-new", title: "Example Podcast" })),
    ),
    http.get("/api/feeds/:feedId/items", () =>
      HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
    ),
    http.post("/api/probe", () =>
      HttpResponse.json(
        probeResult(
          [
            candidate({
              candidate_token: "tok-main",
              source_url: feedUrl,
              title: "Example Podcast",
              item_count: 6,
            }),
            candidate({
              candidate_token: "tok-bonus",
              source_url: bonusUrl,
              title: "Bonus Feed",
              item_count: 2,
            }),
          ],
          "https://podcast.example/",
        ),
      ),
    ),
    http.post("/api/mirrors", async ({ request }) => {
      const body = (await request.json()) as MirrorCreate;
      created.push(body);
      return HttpResponse.json(
        mirror({
          id: "mirror-new",
          title: body.candidate_token === "tok-bonus" ? "Bonus Feed" : "Example Podcast",
          source_url: body.source_url,
          sync_deletions: body.sync_deletions ?? false,
        }),
        { status: 201 },
      );
    }),
  ];
}

async function submitBar(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(await screen.findByLabelText("Source URL"), text);
  await user.click(within(screen.getByRole("main")).getByRole("button", { name: "Add Source" }));
}

describe("Add Source dialog", () => {
  it("lets the person pick when several Sources are found, then shows the Mirror", async () => {
    const created: MirrorCreate[] = [];
    server.use(...baseHandlers(created));
    const user = userEvent.setup();
    const { history } = renderApp("/mirrors");
    await submitBar(user, "https://podcast.example/");

    const dialog = await screen.findByRole("dialog", { name: "Choose a Source" });
    expect(within(dialog).getByRole("radiogroup", { name: "Sources found" })).toBeInTheDocument();
    await user.click(within(dialog).getByText("Bonus Feed"));
    await user.click(within(dialog).getByRole("button", { name: "Mirror this Source" }));
    expect(await screen.findByRole("dialog", { name: "Mirror created" })).toBeInTheDocument();
    expect(created).toEqual([{ source_url: bonusUrl, candidate_token: "tok-bonus" }]);
    expect(screen.getByLabelText("Mirror Feed URL")).toHaveValue(
      "http://localhost:8080/feeds/mirror-new.xml",
    );
    await user.click(screen.getByRole("link", { name: "Open Mirror" }));
    await waitFor(() => expect(history.location.pathname).toBe("/mirrors/mirror-new"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("creates at once for a single Source, with the operator's default policy", async () => {
    const created: MirrorCreate[] = [];
    server.use(
      http.post("/api/probe", () =>
        HttpResponse.json(
          probeResult([candidate({ candidate_token: "only", source_url: feedUrl, item_count: 6 })]),
        ),
      ),
      ...baseHandlers(created),
    );
    const user = userEvent.setup();
    renderApp("/mirrors");
    await submitBar(user, feedUrl);
    expect(await screen.findByRole("dialog", { name: "Mirror created" })).toBeInTheDocument();
    expect(created).toEqual([{ source_url: feedUrl, candidate_token: "only" }]);
    expect(screen.getByText(/Refresh · Running/)).toBeInTheDocument();
  });

  it("asks before mirroring a lone video, offering the Inbox instead", async () => {
    server.use(
      http.post("/api/probe", () =>
        HttpResponse.json(
          probeResult([candidate({ candidate_token: "vid", source_url: feedUrl, item_count: 1 })]),
        ),
      ),
      ...baseHandlers(),
    );
    const user = userEvent.setup();
    renderApp("/mirrors");
    await submitBar(user, feedUrl);
    const dialog = await screen.findByRole("dialog", { name: "Choose a Source" });
    expect(
      within(dialog).getByRole("button", { name: /Send to Inbox instead/ }),
    ).toBeInTheDocument();
  });

  it("searches when the bar gets a name rather than a URL", async () => {
    server.use(
      http.get("/api/search/podcasts", () => HttpResponse.json({ query: "atp", results: [] })),
      http.get("/api/search/videos", () => HttpResponse.json({ query: "atp", results: [] })),
      ...baseHandlers(),
    );
    const user = userEvent.setup();
    renderApp("/mirrors");
    await submitBar(user, "Accidental Tech");
    const dialog = await screen.findByRole("dialog", { name: "Find a podcast" });
    expect(within(dialog).getByLabelText("Podcast or video name")).toHaveValue("Accidental Tech");
  });

  it("opens the existing Mirror on a feed-exists 409 and shows an unsupported Source", async () => {
    server.use(
      http.get("/api/feeds/:feedId", () =>
        HttpResponse.json(mirror({ id: "mirror-dup", title: "Duplicate", source_url: feedUrl })),
      ),
      http.post("/api/probe", () =>
        HttpResponse.json(
          probeResult([candidate({ candidate_token: "only", source_url: feedUrl, item_count: 4 })]),
        ),
      ),
      http.post("/api/mirrors", () =>
        HttpResponse.json(problem("feed-exists", 409, { existing_feed_id: "mirror-dup" }), {
          status: 409,
          headers: { "Content-Type": "application/problem+json" },
        }),
      ),
      ...baseHandlers(),
    );
    const user = userEvent.setup();
    const { history } = renderApp("/mirrors");
    await submitBar(user, feedUrl);
    await waitFor(() => expect(history.location.pathname).toBe("/mirrors/mirror-dup"));

    server.use(
      http.post("/api/probe", () =>
        HttpResponse.json(
          problem("source-unsupported", 422, {
            detail: "Looks like a media file; send it to an Inbox.",
          }),
          { status: 422, headers: { "Content-Type": "application/problem+json" } },
        ),
      ),
    );
    await user.click(screen.getByRole("link", { name: "Mirrors" }));
    await submitBar(user, "https://media.example/file.mp3");
    expect(await screen.findByText("This URL cannot be mirrored")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send to Inbox instead/ })).toBeInTheDocument();
  });

  it("captures Watch Later with one click and imports selected playlists", async () => {
    const created: MirrorCreate[] = [];
    server.use(
      http.get("/api/inboxes/:inboxId/requests", () =>
        HttpResponse.json({ requests: [], total: 0, limit: 1, offset: 0 }),
      ),
      http.get("/api/youtube/playlists", () =>
        HttpResponse.json({
          playlists: [
            { id: "WL", title: "Watch Later", url: "https://www.youtube.com/playlist?list=WL" },
            {
              id: "PLtalks",
              title: "Talks",
              url: "https://www.youtube.com/playlist?list=PLtalks",
              item_count: 12,
            },
            {
              id: "PLdone",
              title: "Already captured",
              url: "https://www.youtube.com/playlist?list=PLdone",
              captured_feed_id: "mirror-done",
            },
          ],
        }),
      ),
      ...baseHandlers(created),
    );
    const user = userEvent.setup();
    renderApp("/inboxes");
    const card = await screen.findByTestId("capture-card");
    expect(card).toHaveTextContent("Capture from the YouTube app");
    await user.click(
      await within(card).findByRole("button", { name: "Keep Watch Later as an Inbox" }),
    );
    await waitFor(() =>
      expect(created).toEqual([
        {
          source_url: "https://www.youtube.com/playlist?list=WL",
          sync_deletions: true,
          playlist_capture: true,
        },
      ]),
    );
    expect(await screen.findByText("“Example Podcast” captured")).toBeInTheDocument();

    const list = within(card).getByRole("list", { name: "Your playlists" });
    expect(within(list).getByText("Already captured")).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/mirrors/mirror-done",
    );
    await user.click(within(list).getByRole("checkbox", { name: /Talks/ }));
    await user.click(within(card).getByRole("button", { name: "Import 1 selected playlist" }));
    await waitFor(() => expect(created).toHaveLength(2));
    expect(created[1]).toEqual({
      source_url: "https://www.youtube.com/playlist?list=PLtalks",
      sync_deletions: true,
      playlist_capture: true,
    });
  });

  it("explains what to do when the playlists cannot be listed", async () => {
    server.use(
      http.get("/api/inboxes/:inboxId/requests", () =>
        HttpResponse.json({ requests: [], total: 0, limit: 1, offset: 0 }),
      ),
      http.get("/api/youtube/playlists", () =>
        HttpResponse.json(
          problem("source-unsupported", 422, { detail: "Sign in to see your playlists" }),
          { status: 422, headers: { "Content-Type": "application/problem+json" } },
        ),
      ),
      ...baseHandlers(),
    );
    renderApp("/inboxes");
    const hint = await screen.findByTestId("capture-error");
    expect(hint).toHaveTextContent("Sign in to see your playlists");
    expect(within(hint).getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );
  });
});
