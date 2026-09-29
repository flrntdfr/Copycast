import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { LiveBadge } from "./LiveBadge";
import { item } from "@/test/factories";
import { renderWithProviders } from "@/test/render";

describe("LiveBadge", () => {
  it.each([
    ["is_live", "Live", "destructive"],
    ["is_upcoming", "Upcoming", "secondary"],
    ["post_live", "Recording being processed", "outline"],
  ] as const)("flags a %s stream as text", (status, text, variant) => {
    renderWithProviders(<LiveBadge item={item({ state: "available", live_status: status })} />);
    const badge = screen.getByText(text).closest("[data-live-status]");
    expect(badge).toHaveAttribute("data-live-status", status);
    expect(badge).toHaveAttribute("data-variant", variant);
  });

  it("explains that the stream is archived once its recording is published", async () => {
    renderWithProviders(<LiveBadge item={item({ state: "available", live_status: "is_live" })} />);
    const user = userEvent.setup();
    await user.hover(screen.getByText("Live"));
    const hints = await screen.findAllByText("Archived once the recording is published");
    expect(hints.length).toBeGreaterThan(0);
  });

  it.each([["was_live"], ["not_live"], [null]] as const)(
    "shows nothing for a %s item",
    (status) => {
      const { container } = renderWithProviders(
        <LiveBadge item={item({ state: "archived", live_status: status })} />,
      );
      expect(container.querySelector("[data-live-status]")).toBeNull();
    },
  );
});
