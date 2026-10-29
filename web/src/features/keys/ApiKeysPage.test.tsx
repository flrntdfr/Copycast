import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { ApiKeyCreate, ApiKeyRead } from "@/api/types";
import { envBlock } from "./NewApiKeyDialog";
import { about, apiKey } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

function handlers(
  state: { keys: ApiKeyRead[]; created: ApiKeyCreate[]; revoked: string[] },
  authEnabled = true,
) {
  return [
    http.get("/api/about", () => HttpResponse.json(about({ auth_enabled: authEnabled }))),
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.get("/api/keys", () => HttpResponse.json({ keys: state.keys })),
    http.post("/api/keys", async ({ request }) => {
      const body = (await request.json()) as ApiKeyCreate;
      state.created.push(body);
      const key = apiKey({
        id: "00000000-0000-4000-8000-000000000099",
        name: body.name,
        scope: body.scope ?? "write",
      });
      state.keys = [key, ...state.keys];
      return HttpResponse.json(
        { key, secret: "cck_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0" },
        { status: 201 },
      );
    }),
    http.delete("/api/keys/:keyId", ({ params }) => {
      state.revoked.push(String(params.keyId));
      state.keys = state.keys.filter((key) => key.id !== params.keyId);
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}

describe("MCP page", () => {
  it("lists keys with scope, prefix and last use, and warns while auth is off", async () => {
    const state = {
      keys: [
        apiKey({
          id: "k1",
          name: "Claude Code",
          scope: "write",
          last_used_at: "2024-01-15T09:30:00Z",
        }),
        apiKey({ id: "k2", name: "Read-only agent", scope: "read", prefix: "cck_zzzzzzzz" }),
      ],
      created: [],
      revoked: [],
    };
    server.use(...handlers(state, false));
    renderApp("/keys");

    expect(await screen.findByRole("heading", { name: "MCP" })).toBeInTheDocument();
    expect(screen.getByText("Authentication is off")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("key-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Claude Code");
    expect(rows[0]).toHaveTextContent("Write");
    expect(rows[0]).toHaveTextContent("cck_a1b2c3d4…");
    const times = within(rows[0]!).getAllByRole("time");
    expect(times.at(-1)).toHaveAttribute("datetime", "2024-01-15T09:30:00Z");
    expect(rows[1]).toHaveTextContent("Read");
    expect(rows[1]).toHaveTextContent("never");
  });

  it("mints a key, shows the .env block once, then revokes it", async () => {
    const state = {
      keys: [] as ApiKeyRead[],
      created: [] as ApiKeyCreate[],
      revoked: [] as string[],
    };
    server.use(...handlers(state));
    const user = userEvent.setup();
    renderApp("/keys");

    expect(await screen.findByText("No keys yet")).toBeInTheDocument();
    expect(screen.queryByText("Authentication is off")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New key" }));
    const dialog = await screen.findByRole("dialog", { name: "New key" });
    await user.type(within(dialog).getByLabelText("Name"), "Laptop");
    await user.click(within(dialog).getByRole("radio", { name: /Full/ }));
    await user.click(within(dialog).getByRole("button", { name: "Create key" }));

    await waitFor(() => expect(state.created).toEqual([{ name: "Laptop", scope: "full" }]));
    const shown = await screen.findByRole("dialog", { name: "Key “Laptop” created" });
    expect(within(shown).queryByText(/shown only this once/)).not.toBeInTheDocument();
    expect(within(shown).getByLabelText("Environment variables")).toHaveValue(
      "COPYCAST_MCP_URL=http://localhost:8080/mcp\nCOPYCAST_MCP_KEY=cck_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0",
    );
    expect(within(shown).getByRole("button", { name: "Copy" })).toBeInTheDocument();
    await user.click(within(shown).getByRole("button", { name: "Done" }));

    // Exactly once: reopening the dialog starts a fresh form, the secret is gone.
    await user.click(screen.getByRole("button", { name: "New key" }));
    const again = await screen.findByRole("dialog", { name: "New key" });
    expect(within(again).getByLabelText("Name")).toHaveValue("");
    expect(within(again).queryByLabelText("Environment variables")).not.toBeInTheDocument();
    await user.click(within(again).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const row = await screen.findByTestId("key-row");
    expect(row).toHaveTextContent("Laptop");
    expect(row).toHaveTextContent("Full");
    await user.click(within(row).getByRole("button", { name: "Revoke Laptop" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Revoke “Laptop”?" });
    await user.click(within(confirm).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(state.revoked).toEqual(["00000000-0000-4000-8000-000000000099"]));
    expect(await screen.findByText("No keys yet")).toBeInTheDocument();
  });
});

describe("envBlock", () => {
  it("names the MCP URL and the secret under the documented variable names", () => {
    expect(envBlock("https://copycast.example/mcp", "cck_secret")).toBe(
      "COPYCAST_MCP_URL=https://copycast.example/mcp\nCOPYCAST_MCP_KEY=cck_secret",
    );
  });
});
