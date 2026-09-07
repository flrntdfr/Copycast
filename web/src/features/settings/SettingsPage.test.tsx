import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { EngineCookiesRead, EngineCookiesWrite } from "@/api/types";
import { about, engineCookies } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const COOKIES = ".youtube.com\tTRUE\t/\tTRUE\t1790000000\tSID\tsecret";

function handlers(state: { stored: EngineCookiesRead; written: string[]; deleted: number }) {
  return [
    http.get("/api/about", () => HttpResponse.json(about())),
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.get("/api/engine/cookies", () => HttpResponse.json(state.stored)),
    http.get("/api/settings/defaults", () =>
      HttpResponse.json({ language: null, min_duration_seconds: null }),
    ),
    http.put("/api/engine/cookies", async ({ request }) => {
      const body = (await request.json()) as EngineCookiesWrite;
      state.written.push(body.content);
      if (!body.content.includes("\t")) {
        return HttpResponse.json(
          {
            type: "urn:copycast:problem:invalid-cookies",
            title: "Not a cookie file",
            status: 422,
            detail: "line 1 has 1 tab-separated fields",
          },
          { status: 422, headers: { "Content-Type": "application/problem+json" } },
        );
      }
      state.stored = engineCookies({
        present: true,
        size_bytes: 61,
        updated_at: "2024-01-15T09:00:00Z",
        cookie_count: 1,
        domains: ["youtube.com"],
        youtube: true,
      });
      return HttpResponse.json(state.stored);
    }),
    http.delete("/api/engine/cookies", () => {
      state.deleted += 1;
      state.stored = engineCookies();
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}

describe("Settings page", () => {
  it("stores a pasted cookie file, shows what it covers, and removes it after confirmation", async () => {
    const state = { stored: engineCookies(), written: [] as string[], deleted: 0 };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp("/settings");

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    const status = screen.getByTestId("cookies-status");
    await waitFor(() => expect(status).toHaveTextContent("No cookies stored"));

    const form = screen.getByRole("form", { name: "Store cookies" });
    const box = within(form).getByLabelText("Cookie file (Netscape cookies.txt)");
    expect(within(form).getByRole("button", { name: "Store cookies" })).toBeDisabled();
    await user.click(box);
    await user.paste(COOKIES);
    await user.click(within(form).getByRole("button", { name: "Store cookies" }));
    await waitFor(() => expect(state.written).toEqual([COOKIES]));
    await waitFor(() => expect(status).toHaveTextContent("1 cookies stored"));
    expect(status).toHaveTextContent("youtube.com");
    expect(box).toHaveValue("");
    expect(await screen.findByText("Cookies stored")).toBeInTheDocument();

    await user.click(within(form).getByRole("button", { name: "Remove…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove the stored cookies?" });
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(state.deleted).toBe(1));
    await waitFor(() => expect(status).toHaveTextContent("No cookies stored"));
  });

  it("surfaces a rejected file as a toast and keeps the text for correction", async () => {
    const state = { stored: engineCookies(), written: [] as string[], deleted: 0 };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp("/settings");
    const form = await screen.findByRole("form", { name: "Store cookies" });
    const box = within(form).getByLabelText("Cookie file (Netscape cookies.txt)");
    await user.click(box);
    await user.paste("SID=abc; HSID=def");
    await user.click(within(form).getByRole("button", { name: "Store cookies" }));
    await waitFor(() => expect(state.written).toHaveLength(1));
    expect(await screen.findByText("Not a cookie file")).toBeInTheDocument();
    expect(box).toHaveValue("SID=abc; HSID=def");
  });
});
