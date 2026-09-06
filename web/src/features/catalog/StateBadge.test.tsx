import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StateBadge } from "./StateBadge";
import { canArchive, canDelete, catalogStateOf, displayNumber } from "./catalog-state";
import { item } from "@/test/factories";
import { renderWithProviders } from "@/test/render";
import { recordProgress, resetProgress } from "@/stores/progress";

describe("catalogStateOf", () => {
  it("derives the six visible states plus hidden tombstones", () => {
    expect(catalogStateOf({ state: "archived", listed: true })).toBe("listed");
    expect(catalogStateOf({ state: "archived", listed: false })).toBe("delisted");
    expect(catalogStateOf({ state: "available", listed: true })).toBe("available");
    expect(catalogStateOf({ state: "deleted", listed: true })).toBe("available");
    expect(catalogStateOf({ state: "deleted", listed: false })).toBe("hidden");
    expect(catalogStateOf({ state: "wanted", listed: true })).toBe("queued");
    expect(catalogStateOf({ state: "archiving", listed: true })).toBe("archiving");
    expect(catalogStateOf({ state: "failed", listed: true })).toBe("failed");
  });

  it("knows which rows can be archived or deleted", () => {
    expect(canArchive({ state: "available" })).toBe(true);
    expect(canArchive({ state: "deleted" })).toBe(true);
    expect(canArchive({ state: "failed" })).toBe(true);
    expect(canArchive({ state: "archived" })).toBe(false);
    expect(canDelete({ state: "archived" })).toBe(true);
    expect(canDelete({ state: "wanted" })).toBe(false);
  });

  it("prefers Source numbering over the Ordinal", () => {
    expect(displayNumber({ source_number: 180, ordinal: 12 })).toEqual({
      value: 180,
      source: true,
    });
    expect(displayNumber({ source_number: null, ordinal: 12 })).toEqual({
      value: 12,
      source: false,
    });
  });
});

describe("StateBadge", () => {
  it.each([
    [item({ state: "archived", listed: true }), "Listed", "listed"],
    [item({ state: "archived", listed: false }), "Delisted", "delisted"],
    [item({ state: "available" }), "Available", "available"],
    [item({ state: "deleted", listed: true }), "Available", "available"],
    [item({ state: "wanted" }), "Queued", "queued"],
    [item({ state: "deleted", listed: false }), "Deleted", "hidden"],
  ])("renders the state as text", (row, text, state) => {
    renderWithProviders(<StateBadge item={row} />);
    const badge = screen.getByText(text);
    expect(badge.closest("[data-state]")).toHaveAttribute("data-state", state);
  });

  it("shows attempts for failed items", () => {
    renderWithProviders(
      <StateBadge item={item({ state: "failed", attempt_count: 3, last_error: "HTTP 500" })} />,
    );
    expect(screen.getByText("Failed (3)")).toBeInTheDocument();
  });

  it("shows the live percent while archiving", () => {
    resetProgress();
    const row = item({ id: "item-live", state: "archiving" });
    recordProgress({
      jobId: "j1",
      feedId: row.feed_id,
      itemId: row.id,
      progress: { phase: "downloading", percent: 37.4 },
    });
    renderWithProviders(<StateBadge item={row} />);
    expect(screen.getByText("Archiving 37%")).toBeInTheDocument();
    resetProgress();
  });
});
