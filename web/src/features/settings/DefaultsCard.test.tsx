import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { MirrorDefaults } from "@/api/types";
import { about, engineCookies, mirrorDefaults } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

describe("Defaults card", () => {
  it("shows the stored defaults and saves language and minimum length", async () => {
    const state = {
      defaults: mirrorDefaults({ min_duration_seconds: 300 }),
      saved: [] as MirrorDefaults[],
    };
    server.use(
      http.get("/api/about", () => HttpResponse.json(about())),
      http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
      http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
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
    const minutes = within(form).getByLabelText("Minimum length (minutes)");
    await waitFor(() => expect(minutes).toHaveValue(5));
    const language = within(form).getByLabelText("Metadata language");
    await user.type(language, "fr");
    await user.clear(minutes);
    await user.type(minutes, "2");
    // The default policy starts from what is stored (Automatic, 7 days) and can change.
    expect(within(form).getByRole("tab", { name: "Automatic" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(form).queryByRole("tab", { name: "Selection" })).not.toBeInTheDocument();
    expect(within(form).getByLabelText("Keep downloaded for")).toHaveValue(7);
    const hours = within(form).getByLabelText("Refresh every (hours)");
    expect(hours).toHaveValue(24);
    await user.clear(hours);
    await user.type(hours, "6");
    await user.click(within(form).getByRole("tab", { name: "Rolling N" }));
    const keep = within(form).getByLabelText("Keep the newest");
    await user.clear(keep);
    await user.type(keep, "5");
    await user.click(within(form).getByRole("button", { name: "Save defaults" }));
    await waitFor(() =>
      expect(state.saved).toEqual([
        {
          language: "fr",
          min_duration_seconds: 120,
          backfill: { mode: "rolling", latest_n: 5 },
          refresh_interval_hours: 6,
        },
      ]),
    );
    expect(await screen.findByText("Defaults saved")).toBeInTheDocument();
  });
});
