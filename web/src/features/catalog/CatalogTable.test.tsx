import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CatalogTable } from "./CatalogTable";
import { catalogSearchDefaults, pageQuery, toCatalogFilters, type CatalogSearch } from "./search";
import type { ItemRead, SelectionRequest } from "@/api/types";
import { item, mirror, problem, selectionResult } from "@/test/factories";
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

/** `count` available items, newest first, titled "Episode 1" (top) to "Episode {count}". */
function listing(count: number): ItemRead[] {
  return Array.from({ length: count }, (_, index) =>
    item({
      id: `ep-${index + 1}`,
      feed_id: feed.id,
      ordinal: count - index,
      source_number: count - index,
      title: `Episode ${index + 1}`,
      state: "available",
    }),
  );
}

/** Serve `all` page by page like `list_items` (offset, limit, a title `q`); returns the requests. */
function serveListing(all: ItemRead[]): URLSearchParams[] {
  const seen: URLSearchParams[] = [];
  server.use(
    http.get("/api/settings/defaults", () =>
      HttpResponse.json({ language: null, min_duration_seconds: null }),
    ),
    http.get("/api/feeds/:feedId/items", ({ request }) => {
      const params = new URL(request.url).searchParams;
      seen.push(params);
      const offset = Number(params.get("offset") ?? 0);
      const limit = Number(params.get("limit") ?? 100);
      const q = params.get("q");
      const matching = q ? all.filter((entry) => entry.title.includes(q)) : all;
      return HttpResponse.json({
        items: matching.slice(offset, offset + limit),
        total: matching.length,
        limit,
        offset,
      });
    }),
  );
  return seen;
}

const offsets = (seen: URLSearchParams[]) => seen.map((params) => params.get("offset"));

/**
 * jsdom lays nothing out: every virtual row gets the virtualizer's estimated height, so rows
 * land where it expects them before and after they are measured.
 */
const ROW_HEIGHT = 49;
function layOutRows() {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.dataset.index !== undefined ? ROW_HEIGHT : 0;
  });
}

function scrollToRow(index: number) {
  Object.defineProperty(window, "scrollY", { configurable: true, value: index * ROW_HEIGHT });
  fireEvent.scroll(window);
}

afterEach(() => {
  Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
});

describe("search helpers", () => {
  it("maps the title filter and the sort to list_items parameters", () => {
    expect(toCatalogFilters(search({ q: "  " }))).toEqual({ sort: "ordinal", order: "desc" });
    const filters = toCatalogFilters(search({ q: " hello " }));
    expect(filters).toEqual({ sort: "ordinal", order: "desc", q: "hello" });
    expect(pageQuery(filters, 2)).toEqual({ ...filters, limit: 100, offset: 200 });
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
    expect(seen[0]?.searchParams.get("sort")).toBe("ordinal");
    expect(seen[0]?.searchParams.get("offset")).toBe("0");
    expect(screen.getByRole("status")).toHaveTextContent("End of the Catalog · 3 items");
    expect(screen.getByRole("table")).toHaveAttribute("aria-rowcount", "5");
  });

  it("flags a stream without a recording next to its state, and nothing once recorded", async () => {
    const streams = [
      item({
        id: "item-live",
        feed_id: feed.id,
        ordinal: 5,
        title: "On air",
        state: "available",
        live_status: "is_live",
      }),
      item({
        id: "item-soon",
        feed_id: feed.id,
        ordinal: 6,
        title: "Tonight",
        state: "available",
        live_status: "is_upcoming",
      }),
      item({
        id: "item-done",
        feed_id: feed.id,
        ordinal: 7,
        title: "Yesterday",
        state: "archived",
        live_status: "was_live",
      }),
    ];
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: streams, total: streams.length, limit: 100, offset: 0 }),
      ),
    );
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findAllByTestId("catalog-row");
    const live = screen.getByText("On air").closest("tr") as HTMLElement;
    expect(within(live).getByText("Available")).toBeInTheDocument();
    expect(within(live).getByText("Live")).toHaveAttribute("data-live-status", "is_live");
    const soon = screen.getByText("Tonight").closest("tr") as HTMLElement;
    expect(within(soon).getByText("Upcoming")).toHaveAttribute("data-live-status", "is_upcoming");
    const done = screen.getByText("Yesterday").closest("tr") as HTMLElement;
    expect(done.querySelector("[data-live-status]")).toBeNull();
  });

  it("marks approximate dates and fetches a missing description when the row opens", async () => {
    const rough = item({
      id: "item-7",
      feed_id: feed.id,
      ordinal: 7,
      title: "Roughly dated",
      state: "available",
      description: null,
      published_at: new Date(Date.now() - 22 * 86_400_000).toISOString(),
      published_at_approximate: true,
      item_url: "https://www.youtube.com/watch?v=rough",
    });
    const fetched: string[] = [];
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [rough, rows[0]], total: 2, limit: 100, offset: 0 }),
      ),
      http.post("/api/feeds/:feedId/items/:itemId/metadata", async ({ params }) => {
        fetched.push(String(params.itemId));
        await delay(150);
        return HttpResponse.json({ ...rough, description: "Now known" });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    const row = (await screen.findByText("Roughly dated")).closest("tr") as HTMLElement;
    expect(within(row).getByTestId("approximate-date")).toHaveTextContent("≈ 3 weeks ago");
    expect(screen.getByText("First").closest("tr")).not.toContainElement(
      screen.queryByTestId("approximate-date"),
    );
    expect(fetched).toEqual([]);
    await user.click(within(row).getByRole("button", { name: "Show details" }));
    expect(await screen.findByText(/Fetching the description/)).toBeInTheDocument();
    await waitFor(() => expect(fetched).toEqual(["item-7"]));
  });

  it("greys out items shorter than the minimum length and says why", async () => {
    const short = item({
      id: "item-5",
      feed_id: feed.id,
      ordinal: 5,
      title: "A Short",
      state: "available",
      duration_seconds: 45,
    });
    const archivedShort = item({
      id: "item-6",
      feed_id: feed.id,
      ordinal: 6,
      title: "Kept Short",
      state: "archived",
      duration_seconds: 45,
    });
    server.use(
      http.get("/api/settings/defaults", () =>
        HttpResponse.json({ language: null, min_duration_seconds: 300 }),
      ),
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({
          items: [...rows, short, archivedShort],
          total: 6,
          limit: 100,
          offset: 0,
        }),
      ),
    );
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    const row = (await screen.findByText("A Short")).closest("tr");
    await waitFor(() => expect(row).toHaveAttribute("data-ignored", "true"));
    expect(row).toHaveClass("opacity-60");
    expect(screen.getByText("Kept Short").closest("tr")).not.toHaveAttribute("data-ignored");
    expect(screen.getByText("Second").closest("tr")).not.toHaveAttribute("data-ignored");
    const user = userEvent.setup();
    await user.hover(within(row as HTMLElement).getByLabelText("Ignored by the policy"));
    expect(
      await screen.findByText(/shorter than the Minimum length in Settings \(5 min\)/),
    ).toBeInTheDocument();
  });

  it("uses the Mirror's own minimum over the default", async () => {
    const own = mirror({ id: "mirror-2", min_duration_seconds: 120 });
    server.use(
      http.get("/api/settings/defaults", () =>
        HttpResponse.json({ language: null, min_duration_seconds: 300 }),
      ),
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({
          items: [
            item({
              id: "s1",
              feed_id: own.id,
              title: "Ninety",
              state: "available",
              duration_seconds: 90,
            }),
            item({
              id: "s2",
              feed_id: own.id,
              title: "Four min",
              state: "available",
              duration_seconds: 240,
            }),
          ],
          total: 2,
          limit: 100,
          offset: 0,
        }),
      ),
    );
    renderWithProviders(
      <CatalogTable feed={own} search={search()} onSearchChange={() => undefined} />,
    );
    expect((await screen.findByText("Ninety")).closest("tr")).toHaveAttribute(
      "data-ignored",
      "true",
    );
    expect(screen.getByText("Four min").closest("tr")).not.toHaveAttribute("data-ignored");
    const user = userEvent.setup();
    await user.hover(screen.getByLabelText("Ignored by the policy"));
    expect(await screen.findByText(/this Mirror's Minimum length \(2 min\)/)).toBeInTheDocument();
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

  it("filters by title and sorts through onSearchChange, with no state facets or page buttons", async () => {
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: rows, total: rows.length, limit: 100, offset: 0 }),
      ),
    );
    const onSearchChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={onSearchChange} />,
    );
    await screen.findAllByTestId("catalog-row");
    expect(screen.queryByRole("group", { name: "Filter by state" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Filter by title" }), "Sec");
    await waitFor(() => expect(onSearchChange).toHaveBeenCalledWith({ q: "Sec" }));
    await user.click(screen.getByRole("button", { name: "Title" }));
    expect(onSearchChange).toHaveBeenCalledWith({ sort: "title", order: "asc" });
    expect(screen.queryByRole("button", { name: /next page/i })).not.toBeInTheDocument();
  });

  it("locks and unlocks an Episode with the padlock; a locked Episode cannot be deleted", async () => {
    const first = item({ id: "item-1", feed_id: feed.id, title: "First", state: "archived" });
    const locked = item({
      id: "item-locked",
      feed_id: feed.id,
      ordinal: 9,
      title: "Kept",
      state: "archived",
      archive_flag: true,
    });
    const flags: { id: string; body: unknown }[] = [];
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [locked, first], total: 2, limit: 100, offset: 0 }),
      ),
      http.put("/api/feeds/:feedId/items/:itemId/archive-flag", async ({ params, request }) => {
        const body = (await request.json()) as { archive_flag: boolean };
        flags.push({ id: String(params.itemId), body });
        const current = params.itemId === locked.id ? locked : first;
        return HttpResponse.json({ ...current, archive_flag: body.archive_flag });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    const keptRow = (await screen.findByText("Kept")).closest("tr") as HTMLElement;
    const firstRow = screen.getByText("First").closest("tr") as HTMLElement;

    // Locked: the padlock is pressed and Delete is disabled, saying why.
    const unlock = within(keptRow).getByRole("button", { name: "Lock", pressed: true });
    expect(unlock).toHaveAttribute("aria-pressed", "true");
    const lockedDelete = within(keptRow).getByRole("button", { name: "Delete" });
    expect(lockedDelete).toHaveAttribute("aria-disabled", "true");
    await user.hover(lockedDelete);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Locked: unlock it to delete");
    await user.click(lockedDelete);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    // Unlocked: the padlock is open and Delete works.
    const lock = within(firstRow).getByRole("button", { name: "Lock", pressed: false });
    expect(lock).toHaveAttribute("aria-pressed", "false");
    expect(within(firstRow).getByRole("button", { name: "Delete" })).toBeEnabled();

    await user.click(lock);
    await waitFor(() =>
      expect(flags).toContainEqual({ id: "item-1", body: { archive_flag: true } }),
    );
    expect(await screen.findByText("Locked")).toBeInTheDocument();
    await user.click(unlock);
    await waitFor(() =>
      expect(flags).toContainEqual({ id: "item-locked", body: { archive_flag: false } }),
    );
    expect(await screen.findByText("Unlocked")).toBeInTheDocument();
  });

  it("leaves locked Episodes out of a bulk delete", async () => {
    const locked = item({
      id: "item-locked",
      feed_id: feed.id,
      ordinal: 9,
      title: "Kept",
      state: "archived",
      archive_flag: true,
    });
    const deleted: string[] = [];
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [locked, rows[0]], total: 2, limit: 100, offset: 0 }),
      ),
      http.delete("/api/feeds/:feedId/items/:itemId", ({ params }) => {
        deleted.push(String(params.itemId));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findByText("Kept");
    await user.click(screen.getByRole("checkbox", { name: "Select all loaded rows" }));
    const bar = await screen.findByRole("region", { name: "Selection" });
    await user.click(within(bar).getByRole("button", { name: "Delete 1" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Delete 1 Episode?");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleted).toEqual(["item-1"]));
  });

  it("says a 409 on delete means the Episode is locked", async () => {
    server.use(
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [rows[0]], total: 1, limit: 100, offset: 0 }),
      ),
      http.delete("/api/feeds/:feedId/items/:itemId", () =>
        HttpResponse.json(problem("conflict", 409), {
          status: 409,
          headers: { "Content-Type": "application/problem+json" },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    const row = (await screen.findByText("First")).closest("tr") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Delete" }));
    await user.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }),
    );
    expect(await screen.findByText("Locked: unlock it to delete")).toBeInTheDocument();
  });
});

describe("CatalogTable lazy loading", () => {
  it("loads the next page as you scroll near the end, and renders only the rows on screen", async () => {
    layOutRows();
    const seen = serveListing(listing(250));
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    expect(await screen.findByText("Episode 1")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("100 of 250 loaded");
    expect(screen.getByRole("table")).toHaveAttribute("aria-rowcount", "-1");
    expect(screen.getAllByTestId("catalog-row").length).toBeLessThan(60);
    expect(offsets(seen)).toEqual(["0"]);

    scrollToRow(75);
    expect(await screen.findByText("Episode 101")).toBeInTheDocument();
    expect(offsets(seen)).toEqual(["0", "100"]);
    expect(screen.queryByText("Episode 1")).not.toBeInTheDocument();
    expect(screen.getByText("Episode 101").closest("tr")).toHaveAttribute("aria-rowindex", "102");

    scrollToRow(240);
    expect(await screen.findByText("Episode 250")).toBeInTheDocument();
    expect(offsets(seen)).toEqual(["0", "100", "200"]);
    expect(screen.getByRole("status")).toHaveTextContent("End of the Catalog · 250 items");
    expect(screen.getByRole("table")).toHaveAttribute("aria-rowcount", "252");
    expect(screen.getAllByTestId("catalog-row").length).toBeLessThan(60);
  });

  it("starts over from the first page when the filter, search or sort changes", async () => {
    layOutRows();
    const seen = serveListing(listing(250));
    const { rerender } = renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findByText("Episode 1");
    scrollToRow(75);
    await screen.findByText("Episode 101");
    scrollToRow(0);
    await screen.findByText("Episode 1");

    rerender(
      <CatalogTable
        feed={feed}
        search={search({ q: "Episode 2" })}
        onSearchChange={() => undefined}
      />,
    );
    // "Episode 2", "Episode 20"–"29" and "Episode 200"–"250".
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("End of the list · 62 items"),
    );
    expect(screen.getByText("Episode 2")).toBeInTheDocument();
    expect(screen.queryByText("Episode 101")).not.toBeInTheDocument();
    const searched = seen.filter((params) => params.get("q"));
    expect(searched.map((params) => [params.get("q"), params.get("offset")])).toEqual([
      ["Episode 2", "0"],
    ]);

    rerender(
      <CatalogTable
        feed={feed}
        search={search({ q: "Episode 2", sort: "title", order: "asc" })}
        onSearchChange={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(seen.filter((params) => params.get("sort") === "title")).toHaveLength(1),
    );
    const sorted = seen.filter((params) => params.get("sort") === "title");
    expect(sorted[0]?.get("offset")).toBe("0");
    expect(sorted[0]?.get("order")).toBe("asc");
  });

  it("refetches only the pages on screen when the Catalog is invalidated", async () => {
    layOutRows();
    const seen = serveListing(listing(300));
    const { client } = renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findByText("Episode 1");
    scrollToRow(75);
    await screen.findByText("Episode 101");
    scrollToRow(175);
    await screen.findByText("Episode 201");
    // Rows 120–155 are rendered: page 1 alone is on screen.
    scrollToRow(130);
    await waitFor(() => expect(screen.queryByText("Episode 201")).not.toBeInTheDocument());
    seen.length = 0;

    await client.invalidateQueries({
      queryKey: ["get", "/api/feeds/{feed_id}/items", { params: { path: { feed_id: feed.id } } }],
    });
    await waitFor(() => expect(offsets(seen)).toEqual(["100"]));

    // Page 0 sat the invalidation out; it refetches once it is back on screen.
    scrollToRow(0);
    await waitFor(() => expect(offsets(seen)).toEqual(["100", "0"]));
  });

  it("selects every loaded row with the header checkbox; rows loaded later start unselected", async () => {
    layOutRows();
    serveListing(listing(150));
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findByText("Episode 1");
    const all = screen.getByRole("checkbox", { name: "Select all loaded rows" });
    await user.click(all);
    const bar = await screen.findByRole("region", { name: "Selection" });
    expect(within(bar).getByText("100 selected")).toBeInTheDocument();
    expect(all).toHaveAttribute("data-state", "checked");

    scrollToRow(75);
    expect(await screen.findByText("Episode 101")).toBeInTheDocument();
    expect(within(bar).getByText("100 selected")).toBeInTheDocument();
    expect(all).toHaveAttribute("data-state", "indeterminate");
    // A row from the new page joins the selection; the first page's stay selected off screen.
    await user.click(screen.getByRole("checkbox", { name: "Select Episode 101" }));
    expect(within(bar).getByText("101 selected")).toBeInTheDocument();
  });

  it("says when a page did not load and retries it", async () => {
    layOutRows();
    const all = listing(150);
    let fail = true;
    server.use(
      http.get("/api/feeds/:feedId/items", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        if (offset > 0 && fail) return HttpResponse.json({ title: "Boom" }, { status: 500 });
        return HttpResponse.json({
          items: all.slice(offset, offset + 100),
          total: all.length,
          limit: 100,
          offset,
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <CatalogTable feed={feed} search={search()} onSearchChange={() => undefined} />,
    );
    await screen.findByText("Episode 1");
    scrollToRow(75);
    expect(await screen.findByText("More items did not load.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Episode 101")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("End of the Catalog · 150 items");
  });
});
