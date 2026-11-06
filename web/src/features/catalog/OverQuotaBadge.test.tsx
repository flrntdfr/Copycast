import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OverQuotaBadge } from "./OverQuotaBadge";
import { item } from "@/test/factories";
import { renderWithProviders } from "@/test/render";

describe("OverQuotaBadge", () => {
  it("flags an item blocked by the quota, and says it retries on its own", async () => {
    renderWithProviders(
      <OverQuotaBadge item={item({ state: "wanted", blocked_reason: "quota" })} />,
    );
    const badge = screen.getByText("Over quota").closest("[data-blocked-reason]");
    expect(badge).toHaveAttribute("data-blocked-reason", "quota");

    await userEvent.setup().hover(screen.getByText("Over quota"));
    const hints = await screen.findAllByText(
      "Over quota: retries automatically when you free space",
    );
    expect(hints.length).toBeGreaterThan(0);
  });

  it.each([[null], [undefined]])("shows nothing when the item is not blocked (%s)", (reason) => {
    const { container } = renderWithProviders(
      <OverQuotaBadge item={item({ state: "wanted", blocked_reason: reason })} />,
    );
    expect(container.querySelector("[data-blocked-reason]")).toBeNull();
  });
});
