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
    http.get("/api/settings/defaults", () =>
      HttpResponse.json(mirrorDefaults({ backfill: { mode: "rolling", latest_n: 3 } })),
    ),
    http.get("/api/jobs", () =>
      HttpResponse.json({
        jobs: [job({ status: "running", feed_id: "mirror-new" })],
        total: 1,
        limit: 100,
        offset: 0,
      }),
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
          follow: body.follow ?? true,
          backfill: {
            mode: body.backfill?.mode ?? "all",
            latest_n: body.backfill?.latest_n ?? null,
          },
          selection:
            body.backfill?.mode === "selection"
              ? { count: 43, expression: body.backfill.selection ?? null, applied_at: null }
              : null,
        }),
        { status: 201, headers: { Location: "/api/feeds/mirror-new" } },
      );
    }),
  ];
}

describe("AddSourceWizard", () => {
  it("walks probe -> candidates -> policy (selection turns follow off) -> created", async () => {
    const created: MirrorCreate[] = [];
    server.use(...baseHandlers(created));
    const user = userEvent.setup();
    renderApp("/mirrors/new?url=https%3A%2F%2Fpodcast.example%2F");

    // Step 1: probing with a Cancel button.
    expect(
      await screen.findByRole("heading", { name: "Looking at the Source" }),
    ).toBeInTheDocument();

    // Step 2: two candidates in a radio group; pick the bonus feed.
    const group = await screen.findByRole("radiogroup", { name: "Sources found" });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    await user.click(screen.getByText("Bonus Feed"));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // Step 3: policy, starting from the operator's default (Rolling 3 here).
    expect(await screen.findByText("Everything (2)")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Rolling N" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.getByLabelText("Keep the newest")).toHaveValue(3);
    // Selection switches Follow off.
    const follow = screen.getByRole("switch", { name: "Follow" });
    expect(follow).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("tab", { name: "Selection" }));
    await waitFor(() => expect(follow).toHaveAttribute("aria-checked", "false"));
    const expression = screen.getByPlaceholderText("1-42, 180");
    await user.type(expression, "1-42, 180");
    expect(await screen.findByText("43 numbers")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create Mirror" }));

    // Step 4: created with the feed URL, the hint and the selection summary.
    expect(await screen.findByText("Mirror created")).toBeInTheDocument();
    expect(screen.getByLabelText("Mirror Feed URL")).toHaveValue(
      "http://localhost:8080/feeds/mirror-new.xml",
    );
    expect(screen.getByText(/Add by URL/)).toBeInTheDocument();
    expect(screen.getByText(/43 Episodes queued/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Mirror" })).toHaveAttribute(
      "href",
      "/mirrors/mirror-new",
    );

    expect(created).toEqual([
      {
        source_url: bonusUrl,
        candidate_token: "tok-bonus",
        backfill: { mode: "selection", selection: "1-42, 180" },
        follow: false,
      },
    ]);
  });

  it("skips the candidate step for a single Source and shows the first Refresh", async () => {
    server.use(
      http.post("/api/probe", () =>
        HttpResponse.json(
          probeResult([candidate({ candidate_token: "only", source_url: feedUrl, item_count: 6 })]),
        ),
      ),
      ...baseHandlers(),
    );
    const user = userEvent.setup();
    renderApp("/mirrors/new?url=" + encodeURIComponent(feedUrl));
    expect(await screen.findByText("Everything (6)")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup", { name: "Sources found" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create Mirror" }));
    expect(await screen.findByText("Mirror created")).toBeInTheDocument();
    expect(await screen.findByText(/Refresh · Running/)).toBeInTheDocument();
  });

  it("navigates to the existing Mirror when the Source is already mirrored", async () => {
    const existing = mirror({ id: "mirror-existing", title: "Already here", source_url: feedUrl });
    server.use(
      http.get("/api/feeds", () => HttpResponse.json({ feeds: [existing] })),
      http.get("/api/feeds/:feedId", () => HttpResponse.json(existing)),
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
      ),
      http.post("/api/probe", () =>
        HttpResponse.json(
          probeResult([candidate({ candidate_token: "only", source_url: feedUrl })]),
        ),
      ),
      ...baseHandlers(),
    );
    const { history } = renderApp("/mirrors/new?url=" + encodeURIComponent(feedUrl));
    await waitFor(() => expect(history.location.pathname).toBe("/mirrors/mirror-existing"));
    expect(await screen.findByRole("heading", { name: "Already here" })).toBeInTheDocument();
  });

  it("opens the existing Mirror on a feed-exists 409 from create", async () => {
    const existing = mirror({ id: "mirror-dup", title: "Duplicate", source_url: feedUrl });
    server.use(
      http.get("/api/feeds/:feedId", () => HttpResponse.json(existing)),
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
      ),
      http.post("/api/probe", () =>
        HttpResponse.json(
          probeResult([candidate({ candidate_token: "only", source_url: feedUrl })]),
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
    const { history } = renderApp("/mirrors/new?url=" + encodeURIComponent(feedUrl));
    await user.click(await screen.findByRole("button", { name: "Create Mirror" }));
    await waitFor(() => expect(history.location.pathname).toBe("/mirrors/mirror-dup"));
  });

  it("shows an unsupported Source with a Send to Inbox alternative", async () => {
    server.use(
      http.post("/api/probe", () =>
        HttpResponse.json(
          problem("source-unsupported", 422, {
            detail: "Looks like a media file; send it to an Inbox.",
          }),
          {
            status: 422,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
      ),
      ...baseHandlers(),
    );
    renderApp("/mirrors/new?url=https%3A%2F%2Fmedia.example%2Ffile.mp3");
    expect(await screen.findByText("This URL cannot be mirrored")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /Send to Inbox instead/ }),
    ).toBeInTheDocument();
  });
});
