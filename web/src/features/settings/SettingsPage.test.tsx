import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ClearFeedResult, EngineCookiesWrite, FeedRead, MirrorDefaults } from "@/api/types";
import { about, cookieFile, engineCookies, bucket, mirror, mirrorDefaults } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const COOKIES = ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tsecret";

function baseHandlers(feeds: FeedRead[] = []) {
  return [
    http.get("/api/about", () => HttpResponse.json(about())),
    http.get("/api/feeds", ({ request }) => {
      const kind = new URL(request.url).searchParams.get("kind");
      return HttpResponse.json({ feeds: kind ? feeds.filter((f) => f.kind === kind) : feeds });
    }),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
  ];
}

describe("Settings page: Authentications", () => {
  it("unlocks a service from a picked cookie file and removes the file after confirmation", async () => {
    const state = {
      stored: engineCookies(),
      written: [] as EngineCookiesWrite[],
      deleted: [] as string[],
    };
    server.use(
      ...baseHandlers(),
      http.get("/api/settings/defaults", () => HttpResponse.json(mirrorDefaults())),
      http.get("/api/engine/cookies", () => HttpResponse.json(state.stored)),
      http.put("/api/engine/cookies", async ({ request }) => {
        const body = (await request.json()) as EngineCookiesWrite;
        state.written.push(body);
        state.stored = engineCookies({ files: [cookieFile({ name: "youtube" })] });
        return HttpResponse.json(state.stored);
      }),
      http.delete("/api/engine/cookies", ({ request }) => {
        state.deleted.push(new URL(request.url).searchParams.get("name") ?? "");
        state.stored = engineCookies();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderApp("/settings");

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    const youtube = await screen.findByTestId("service-youtube");
    expect(youtube).toHaveAttribute("data-authenticated", "false");
    expect(screen.getByTestId("service-mixcloud")).toHaveAttribute("data-authenticated", "false");
    expect(screen.queryByRole("table", { name: "Cookie files" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Locked")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add cookies.txt…" })).toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Paste…" })).not.toBeInTheDocument();
    await user.upload(
      screen.getByLabelText("Pick a cookies.txt file"),
      new File([COOKIES], "youtube.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(state.written).toEqual([{ content: COOKIES, name: "youtube.txt" }]));
    await waitFor(() => expect(youtube).toHaveAttribute("data-authenticated", "true"));
    expect(screen.getByTestId("cookie-file-youtube")).toHaveTextContent(
      "data/engine/cookies/youtube.txt",
    );
    expect(await screen.findByText("Cookie file stored")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove youtube.txt" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove youtube.txt?" });
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(state.deleted).toEqual(["youtube"]));
    await waitFor(() => expect(youtube).toHaveAttribute("data-authenticated", "false"));
  });
});

describe("Settings page: Mirrors", () => {
  it("saves each change without a button: stepper, language, Ephemeral days, Archive backfill", async () => {
    const state = {
      defaults: mirrorDefaults({ min_duration_seconds: 300, refresh_interval_hours: 1 }),
      saved: [] as MirrorDefaults[],
    };
    server.use(
      ...baseHandlers(),
      http.get("/api/engine/cookies", () => HttpResponse.json(engineCookies())),
      http.get("/api/settings/defaults", () => HttpResponse.json(state.defaults)),
      http.put("/api/settings/defaults", async ({ request }) => {
        const body = (await request.json()) as MirrorDefaults;
        state.saved.push(body);
        state.defaults = body;
        return HttpResponse.json(body);
      }),
    );
    const user = userEvent.setup();
    renderApp("/settings");
    const form = await screen.findByRole("form", { name: "Mirror defaults" });
    expect(within(form).queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
    const hours = within(form).getByLabelText("Refresh every");
    expect(hours).toHaveValue(1);
    expect(within(form).getByRole("button", { name: "One hour less" })).toBeDisabled();
    expect(within(form).getByLabelText("Exclude episodes shorter than")).toHaveValue(5);

    await user.click(within(form).getByRole("button", { name: "One hour more" }));
    await user.click(within(form).getByRole("button", { name: "One hour more" }));
    expect(hours).toHaveValue(3);
    await waitFor(() => expect(state.saved.at(-1)?.refresh_interval_hours).toBe(3), {
      timeout: 2000,
    });
    // Two clicks in quick succession are one save.
    expect(state.saved).toHaveLength(1);

    await user.type(within(form).getByLabelText("Default language"), "fr");
    await waitFor(() => expect(state.saved.at(-1)?.language).toBe("fr"), { timeout: 2000 });

    expect(within(form).getByRole("tab", { name: "Ephemeral" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const days = within(form).getByLabelText("Delete after");
    expect(days).toHaveValue(7);
    await user.clear(days);
    await user.type(days, "30");
    await waitFor(
      () => expect(state.saved.at(-1)?.backfill).toEqual({ mode: "automatic", retention_days: 30 }),
      { timeout: 2000 },
    );

    // Archive starts with Backfill off: only what the Source publishes from now on.
    await user.click(within(form).getByRole("tab", { name: "Archive" }));
    const backfill = within(form).getByRole("switch", { name: /Backfill/ });
    expect(backfill).not.toBeChecked();
    await waitFor(() => expect(state.saved.at(-1)?.backfill).toEqual({ mode: "new" }), {
      timeout: 2000,
    });
    await user.click(backfill);
    await waitFor(() => expect(state.saved.at(-1)?.backfill).toEqual({ mode: "all" }), {
      timeout: 2000,
    });
    expect(state.saved.at(-1)).toEqual({
      language: "fr",
      min_duration_seconds: 300,
      backfill: { mode: "all" },
      refresh_interval_hours: 3,
    });
  });
});

describe("Settings page: Buckets", () => {
  const news = mirror({ id: "m-news", title: "News", storage_bytes: 12_000_000 });
  const art = mirror({ id: "m-art", title: "Art", storage_bytes: 3_000_000 });
  const capture = mirror({
    id: "m-wl",
    title: "Watch later",
    playlist_capture: true,
    storage_bytes: 1_000_000,
  });
  const box = bucket({ id: "i-default", title: "Copycast", is_default: true, storage_bytes: 0 });
  const agent = bucket({ id: "i-agent", title: "Audiobooks", storage_bytes: 50_000_000 });

  function handlers(state: { cleared: boolean[]; deleted: string[] }, preview: ClearFeedResult) {
    return [
      http.get("/api/feeds", ({ request }) => {
        const kind = new URL(request.url).searchParams.get("kind");
        // The api sorts; this fake answers in the Feeds page's order by title.
        return HttpResponse.json({
          feeds: kind === "mirror" ? [art, news, capture] : [art, box, agent, news, capture],
        });
      }),
      ...baseHandlers(),
      http.get("/api/engine/cookies", () => HttpResponse.json(engineCookies())),
      http.get("/api/settings/defaults", () => HttpResponse.json(mirrorDefaults())),
      http.post("/api/feeds/:feedId/clear", async ({ request }) => {
        const body = (await request.json()) as { dry_run: boolean };
        state.cleared.push(body.dry_run);
        return HttpResponse.json({ ...preview, dry_run: body.dry_run });
      }),
      http.delete("/api/feeds/:feedId", ({ params }) => {
        state.deleted.push(String(params.feedId));
        return new HttpResponse(null, { status: 204 });
      }),
    ];
  }

  it("lists the Mirrors first, then the Buckets and captured playlists, with sizes", async () => {
    server.use(
      ...handlers(
        { cleared: [], deleted: [] },
        {
          matched: 0,
          deleted_count: 0,
          bytes_freed: 0,
          dry_run: true,
          kept_count: 0,
          kept_bytes: 0,
        },
      ),
    );
    renderApp("/settings");
    const table = await screen.findByRole("table", { name: "Buckets" });
    await waitFor(() => expect(within(table).getAllByTestId(/^bucket-/)).toHaveLength(5));
    expect(
      within(table)
        .getAllByTestId(/^bucket-/)
        .map((row) => row.dataset.testid),
    ).toEqual([
      "bucket-m-art",
      "bucket-m-news",
      "bucket-i-default",
      "bucket-i-agent",
      "bucket-m-wl",
    ]);
    expect(within(table).getByTestId("bucket-m-news")).toHaveTextContent("12 MB");
    expect(screen.getByText("66 MB in all")).toBeInTheDocument();
  });

  it("deletes a bucket without flagged Episodes after the plain confirmation", async () => {
    const state = { cleared: [] as boolean[], deleted: [] as string[] };
    server.use(
      ...handlers(state, {
        matched: 3,
        deleted_count: 0,
        bytes_freed: 3_000_000,
        dry_run: true,
        kept_count: 0,
        kept_bytes: 0,
      }),
    );
    const user = userEvent.setup();
    renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Delete Art" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Mirror “Art”?" });
    await waitFor(() => expect(state.cleared).toEqual([true]));
    expect(within(dialog).queryByTestId("bucket-archive-warning")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete Mirror" }));
    await waitFor(() => expect(state.deleted).toEqual(["m-art"]));
  });

  it("warns about Episodes flagged Archive and can delete only the rest", async () => {
    const state = { cleared: [] as boolean[], deleted: [] as string[] };
    server.use(
      ...handlers(state, {
        matched: 4,
        deleted_count: 4,
        bytes_freed: 40_000_000,
        dry_run: true,
        kept_count: 1,
        kept_bytes: 10_000_000,
      }),
    );
    const user = userEvent.setup();
    renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Delete Audiobooks" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete Bucket “Audiobooks”?" });
    expect(await within(dialog).findByTestId("bucket-archive-warning")).toHaveTextContent(
      "1 Episode flagged Archive (10 MB)",
    );
    expect(within(dialog).getByRole("button", { name: "Delete everything" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Delete the rest" }));
    await waitFor(() => expect(state.cleared).toEqual([true, false]));
    expect(state.deleted).toEqual([]);
    expect(await screen.findByText("4 Episodes deleted")).toBeInTheDocument();
  });

  it("never offers to delete the default Bucket, only to empty it", async () => {
    const state = { cleared: [] as boolean[], deleted: [] as string[] };
    server.use(
      ...handlers(state, {
        matched: 2,
        deleted_count: 0,
        bytes_freed: 2_000_000,
        dry_run: true,
        kept_count: 0,
        kept_bytes: 0,
      }),
    );
    const user = userEvent.setup();
    renderApp("/settings");
    await user.click(await screen.findByRole("button", { name: "Delete Copycast" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Empty the Bucket “Copycast”?" });
    expect(within(dialog).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    await user.click(await within(dialog).findByRole("button", { name: "Empty the Bucket" }));
    await waitFor(() => expect(state.cleared).toEqual([true, false]));
  });
});
