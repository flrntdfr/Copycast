import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { systemStatus } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

function baseHandlers() {
  return [
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
  ];
}

describe("StatusIndicator", () => {
  it("shows the worst component and opens the ones that need attention", async () => {
    server.use(
      ...baseHandlers(),
      http.get("/api/status", () =>
        HttpResponse.json(
          systemStatus({
            state: "warn",
            components: [
              {
                id: "database",
                name: "Database",
                state: "ok",
                summary: "Postgres is up",
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
    expect(within(crawlers).getByText("Sign in to confirm you're not a bot")).toBeVisible();
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
});
