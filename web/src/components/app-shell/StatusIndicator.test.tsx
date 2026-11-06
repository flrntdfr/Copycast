import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { describeMeter } from "./StatusIndicator";
import { problem, storageComponent, systemStatus } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

function baseHandlers() {
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
  ];
}

describe("StatusIndicator", () => {
  it("shows the worst component, the check time and one entry per crawler", async () => {
    server.use(
      ...baseHandlers(),
      http.get("/api/status", () =>
        HttpResponse.json(
          systemStatus({
            state: "warn",
            components: [
              {
                id: "database",
                name: "Postgres",
                state: "ok",
                summary: "Up",
                facts: [],
              },
              {
                id: "crawlers",
                name: "Crawlers",
                state: "warn",
                summary: "1 of 14 Refreshes failed in 24 h",
                facts: [
                  {
                    label: "YouTube",
                    value: "3 ok, 1 failed",
                    detail: "Sign in to confirm you're not a bot",
                    state: "warn",
                  },
                ],
              },
            ],
          }),
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp("/");

    const trigger = await screen.findByRole("button", {
      name: "System status: Something needs attention",
    });
    await user.click(trigger);
    const list = await screen.findByRole("list", { name: "Components" });
    expect(within(list).getByTestId("status-database")).toHaveAttribute("data-state", "ok");
    const crawlers = within(list).getByTestId("status-crawlers");
    expect(crawlers).toHaveTextContent("1 of 14 Refreshes failed in 24 h");
    expect(within(crawlers).getByTestId("crawler-YouTube")).toHaveTextContent("3 ok, 1 failed");
    expect(within(crawlers).getByTestId("crawler-YouTube")).toHaveAttribute(
      "title",
      "Sign in to confirm you're not a bot",
    );
    expect(document.querySelector('time[datetime="2024-01-15T09:00:00Z"]')).not.toBeNull();
    expect(within(list).getByTestId("status-live")).toHaveTextContent("Live updates");
  });

  it("turns red when the API cannot be reached", async () => {
    server.use(
      ...baseHandlers(),
      http.get("/api/status", () => HttpResponse.error()),
    );
    renderApp("/");
    expect(
      await screen.findByRole("button", { name: "System status: Something is broken" }),
    ).toBeInTheDocument();
  });

  async function openWith(...components: Parameters<typeof systemStatus>[0][]) {
    server.use(
      ...baseHandlers(),
      http.get("/api/status", () => HttpResponse.json(systemStatus(...components))),
    );
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /^System status/ }));
    return screen.findByRole("list", { name: "Components" });
  }

  it.each([
    ["ok", 3_200_000_000, "3.2 GB of 10 GB · 32 %", "All systems normal"],
    ["warn", 9_000_000_000, "9 GB of 10 GB · 90 %", "Something needs attention"],
    ["error", 10_400_000_000, "10.4 GB of 10 GB · 104 %", "Something is broken"],
  ] as const)(
    "shows the Storage row as a bar and a percentage (%s)",
    async (state, used, words, overall) => {
      const list = await openWith({
        state: state === "ok" ? "ok" : state,
        components: [storageComponent({ used_bytes: used })],
      });
      const row = within(list).getByTestId("status-quota");
      expect(row).toHaveAttribute("data-state", state);
      expect(row).toHaveTextContent("Storage");
      expect(row).toHaveTextContent(words);
      const bar = within(row).getByRole("progressbar", { name: "Storage" });
      expect(bar).toHaveAttribute("aria-valuenow", String(Math.min(100, Math.round(used / 1e8))));
      expect(screen.getByRole("button", { name: `System status: ${overall}` })).toBeInTheDocument();
    },
  );

  it("shows only the used size for an unlimited User, without a bar", async () => {
    const list = await openWith({
      components: [storageComponent({ used_bytes: 1_500_000_000, quota_bytes: null })],
    });
    const row = within(list).getByTestId("status-quota");
    expect(row).toHaveTextContent("1.5 GB used (unlimited)");
    expect(within(row).queryByRole("progressbar")).toBeNull();
  });

  it("lists every User for an Admin, removed ones included", async () => {
    const list = await openWith({
      state: "error",
      components: [
        storageComponent({
          used_bytes: 1_000_000_000,
          quota_bytes: null,
          state: "error",
          facts: [
            {
              label: "florent",
              value: "1 GB used (unlimited)",
              state: "ok",
              meter: { used_bytes: 1_000_000_000, quota_bytes: null, ratio: null },
            },
            {
              label: "emilia",
              value: "10.5 GB of 10 GB (105 %)",
              state: "error",
              meter: { used_bytes: 10_500_000_000, quota_bytes: 10_000_000_000, ratio: 1.05 },
            },
            {
              label: "bob (removed)",
              value: "200 MB of 10 GB (2 %)",
              state: "ok",
              meter: { used_bytes: 200_000_000, quota_bytes: 10_000_000_000, ratio: 0.02 },
            },
          ],
        }),
      ],
    });
    const users = within(list).getByRole("list", { name: "Storage by User" });
    expect(within(users).getAllByRole("listitem")).toHaveLength(3);
    expect(within(users).getByTestId("quota-emilia")).toHaveAttribute("data-state", "error");
    expect(within(users).getByTestId("quota-emilia")).toHaveTextContent("10.5 GB of 10 GB · 105 %");
    expect(within(users).getByTestId("quota-bob (removed)")).toHaveTextContent("200 MB of 10 GB");
    expect(within(users).getByTestId("quota-florent")).toHaveTextContent("1 GB used (unlimited)");
  });

  it("does not call a 401 an outage", async () => {
    server.use(
      ...baseHandlers(),
      http.get("/api/status", () =>
        HttpResponse.json(problem("unauthorized", 401), { status: 401 }),
      ),
    );
    const { client } = renderApp("/");
    await screen.findByRole("button", { name: /^System status/ });
    // The session ends while the page is open: `/api/me` answers 401 from now on.
    server.use(
      http.get("/api/me", () => HttpResponse.json(problem("unauthorized", 401), { status: 401 })),
    );
    await client.refetchQueries({ queryKey: ["get", "/api/status"] });
    // The app leaves for the sign-in page; the dot never claimed the API was unreachable.
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("Unreachable")).toBeNull();
  });
});

describe("describeMeter", () => {
  it("words a meter, computing the ratio when the api left it out", () => {
    expect(describeMeter({ used_bytes: 0, quota_bytes: 0, ratio: null })).toEqual({
      text: "0 B of 0 B",
      percent: 100,
    });
    expect(
      describeMeter({ used_bytes: 500_000_000, quota_bytes: 2_000_000_000, ratio: null }),
    ).toEqual({ text: "500 MB of 2 GB", percent: 25 });
    expect(describeMeter({ used_bytes: 0, quota_bytes: null, ratio: null })).toEqual({
      text: "0 B used (unlimited)",
      percent: null,
    });
  });
});
