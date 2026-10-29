import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { EngineCookiesWrite, MirrorDefaults } from "@/api/types";
import { about, cookieFile, engineCookies, mirrorDefaults } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const COOKIES = ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tsecret";

function baseHandlers() {
  return [
    http.get("/api/about", () => HttpResponse.json(about())),
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
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

describe("Settings page: layout", () => {
  it("shows Authentications, Mirror defaults and the OPML export, and no storage", async () => {
    server.use(
      ...baseHandlers(),
      http.get("/api/engine/cookies", () => HttpResponse.json(engineCookies())),
      http.get("/api/settings/defaults", () => HttpResponse.json(mirrorDefaults())),
    );
    renderApp("/settings");

    expect(await screen.findByRole("heading", { name: /Authentications/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Mirrors/ })).toBeInTheDocument();
    const opml = screen.getByRole("region", { name: "Podcast apps" });
    expect(opml).toHaveTextContent("Overcast, Pocket Casts or AntennaPod");
    const link = within(opml).getByRole("link", { name: "Export OPML" });
    expect(link).toHaveAttribute("href", "/api/feeds.opml");
    expect(link).toHaveAttribute("download", "copycast.opml");

    // Every Feed's storage lives on its own bucket page now.
    expect(screen.queryByRole("table", { name: "Buckets" })).not.toBeInTheDocument();
  });
});
