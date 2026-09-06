import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { CatalogTable } from "./CatalogTable";
import { catalogSearchDefaults, facetParams, toListItemsQuery, type CatalogSearch } from "./search";
import type { SelectionRequest } from "@/api/types";
import { item, mirror, selectionResult } from "@/test/factories";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

const feed = mirror({ id: "mirror-1", title: "Example Podcast" });
const rows = [
  item({
    id: "item-1",
    feed_id: feed.id,
    ordinal: 1,
    source_number: 1,
    title: "First",
    state: "archived",
  }),
  item({
    id: "item-2",
    feed_id: feed.id,
    ordinal: 2,
    source_number: 2,
    title: "Second",
    state: "available",
  }),
  item({
    id: "item-3",
    feed_id: feed.id,
    ordinal: 3,
    source_number: null,
    title: "Third",
    state: "archived",
    listed: false,
  }),
  item({
    id: "item-4",
    feed_id: feed.id,
    ordinal: 4,
    title: "Ghost",
    state: "deleted",
    listed: false,
  }),
];

function search(patch: Partial<CatalogSearch> = {}): CatalogSearch {
  return { ...catalogSearchDefaults, ...patch };
}

describe("search helpers", () => {
  it("maps facets to list_items parameters", () => {
    expect(facetParams("delisted")).toEqual({ state: ["archived"], listed: false });
    expect(facetParams("available")).toEqual({ state: ["available", "deleted"], listed: true });
    expect(facetParams(undefined)).toEqual({});
    expect(toListItemsQuery(search({ page: 3, q: " hello " }))).toEqual({
      sort: "published",
      order: "desc",
      limit: 100,
      offset: 200,
      q: "hello",
    });
  });
});

describe("CatalogTable", () => {
  it("renders rows with Source numbering, states and the Delisted sub-line; hides tombstones", async () => {
    const seen: URL[] = [];
    server.use(
      http.get("/api/feeds/:feedId/items", ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json({ items: rows, total: rows.length, limit: 100, offset: 0 });
      }),
    );
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    const rendered = await screen.findAllByTestId("catalog-row");
    expect(rendered).toHaveLength(3);
    const table = within(screen.getByRole("table"));
    expect(table.getByText("Listed")).toBeInTheDocument();
    expect(table.getByText("Available")).toBeInTheDocument();
    expect(table.getByText("Delisted")).toBeInTheDocument();
    expect(table.getByText("Delisted; kept in the Mirror Feed")).toBeInTheDocument();
    expect(table.getByText("Third").closest("tr")).toHaveTextContent("3");
    expect(screen.queryByText("Ghost")).not.toBeInTheDocument();
    expect(seen[0]?.searchParams.get("limit")).toBe("100");
    expect(seen[0]?.searchParams.get("sort")).toBe("published");
    expect(screen.getByText("1–4 of 4")).toBeInTheDocument();
  });

  it("selects rows (shift-click ranges) and shows the SelectionBar with Archive for archivable rows", async () => {
    const bodies: SelectionRequest[] = [];
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: rows, total: rows.length, limit: 100, offset: 0 }),
      ),
      http.post("/api/mirrors/:feedId/selections", async ({ request }) => {
        const body = (await request.json()) as SelectionRequest;
        bodies.push(body);
        return HttpResponse.json(selectionResult({ resolved: body.item_ids ?? [], jobs: [] }), {
          status: 202,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findAllByTestId("catalog-row");
    await user.click(screen.getByRole("checkbox", { name: "Select First" }));
    await user.keyboard("{Shift>}");
    await user.click(screen.getByRole("checkbox", { name: "Select Third" }));
    await user.keyboard("{/Shift}");
    const bar = await screen.findByRole("region", { name: "Selection" });
    expect(within(bar).getByText("3 selected")).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: /^Archive/ }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ item_ids: ["item-2"], numbering: "source", dry_run: false });
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Selection" })).not.toBeInTheDocument(),
    );
  });

  it("previews a range with a dry run and confirms with the real request", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const bodies: SelectionRequest[] = [];
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: rows, total: rows.length, limit: 100, offset: 0 }),
      ),
      http.post("/api/mirrors/:feedId/selections", async ({ request }) => {
        const body = (await request.json()) as SelectionRequest;
        bodies.push(body);
        const resolved = Array.from({ length: 43 }, (_, i) => `id-${i}`);
        return HttpResponse.json(
          selectionResult({
            resolved,
            already_archived_count: 2,
            dry_run: body.dry_run,
            jobs: body.dry_run ? [] : resolved.slice(2).map(() => ({}) as never),
          }),
          { status: body.dry_run ? 200 : 202 },
        );
      }),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findAllByTestId("catalog-row");
    await user.type(
      screen.getByRole("textbox", { name: "Archive by episode numbers" }),
      "1-42, 180",
    );
    const preview = await screen.findByTestId("dry-run-preview");
    expect(preview).toHaveTextContent("43 Episodes, 2 already archived");
    expect(bodies).toEqual([{ selection: "1-42, 180", numbering: "source", dry_run: true }]);
    const confirm = screen.getByRole("button", { name: "Archive 41" });
    await user.click(confirm);
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({ selection: "1-42, 180", numbering: "source", dry_run: false });
    vi.useRealTimers();
  });

  it("changes the facet and the page through onSearchChange", async () => {
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: rows, total: 250, limit: 100, offset: 0 }),
      ),
    );
    const onSearchChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={onSearchChange} />,
    );
    await screen.findAllByTestId("catalog-row");
    await user.click(screen.getByRole("radio", { name: "Delisted" }));
    expect(onSearchChange).toHaveBeenCalledWith({ state: "delisted", page: 1 });
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(onSearchChange).toHaveBeenCalledWith({ page: 2 });
  });
});
