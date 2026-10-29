import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { ROTATING_PLACEHOLDER_INTERVAL_MS } from "@/components/common/RotatingPlaceholder";
import { ADD_SOURCE_HINTS, AddSourceBar } from "./AddSourceBar";
import { mirrorDefaults } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const STEP = ROTATING_PLACEHOLDER_INTERVAL_MS;

function hint(): HTMLElement | null {
  return screen.queryByTestId("rotating-placeholder");
}

function advance(ms: number): void {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { value: hidden, configurable: true });
}

describe("Add Source bar", () => {
  it("keeps the field labelled and described while the placeholder rotates", () => {
    vi.useFakeTimers();
    render(<AddSourceBar />);
    const field = screen.getByLabelText("Source URL");
    expect(field).toHaveAttribute("placeholder", "");
    expect(field).toHaveAccessibleDescription(
      /podcast feed.*YouTube.*Spotify.*SoundCloud.*podcast name to search/,
    );
    // The overlay is decorative: hidden from assistive tech and never in the way of the pointer.
    expect(hint()).toHaveAttribute("aria-hidden", "true");
    expect(hint()).toHaveClass("pointer-events-none");
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[0]);
  });

  it("cycles through the hints while the field is empty, and hides them once it has text", () => {
    vi.useFakeTimers();
    render(<AddSourceBar />);
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[0]);
    advance(STEP);
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[1]);
    advance(STEP);
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[2]);
    // Round and round.
    advance(STEP * (ADD_SOURCE_HINTS.length - 2));
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[0]);

    const field = screen.getByLabelText("Source URL");
    fireEvent.change(field, { target: { value: "https://podcast.example/feed.xml" } });
    expect(hint()).not.toBeInTheDocument();
    // Nothing ticks while the field holds text; emptying it brings the hints back.
    advance(STEP * 3);
    fireEvent.change(field, { target: { value: "" } });
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[0]);
    advance(STEP);
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[1]);
  });

  it("pauses while the tab is hidden and stops when it unmounts", () => {
    vi.useFakeTimers();
    const { unmount } = render(<AddSourceBar />);
    expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[0]);
    try {
      act(() => {
        setHidden(true);
        document.dispatchEvent(new Event("visibilitychange"));
      });
      advance(STEP * 3);
      expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[0]);
      act(() => {
        setHidden(false);
        document.dispatchEvent(new Event("visibilitychange"));
      });
      advance(STEP);
      expect(hint()).toHaveTextContent(ADD_SOURCE_HINTS[1]);
    } finally {
      Reflect.deleteProperty(document, "hidden");
    }
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("enables Mirror only once there is something to mirror", async () => {
    const user = userEvent.setup();
    render(<AddSourceBar />);
    const button = screen.getByRole("button", { name: "Mirror" });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("Source URL"), "   ");
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("Source URL"), "atp");
    expect(button).toBeEnabled();
  });

  it("hints at the sites the Engine supports, then a name to search", () => {
    expect(ADD_SOURCE_HINTS[0]).toMatch(/podcast RSS feed/);
    for (const site of [
      "YouTube channel",
      "YouTube playlist",
      "Apple Podcasts",
      "Spotify",
      "SoundCloud",
      "Vimeo",
      "Twitch",
      "Bandcamp",
      "Mixcloud",
      "Dailymotion",
      "Internet Archive",
    ]) {
      expect(ADD_SOURCE_HINTS.some((hint) => hint.includes(site))).toBe(true);
    }
    expect(ADD_SOURCE_HINTS.at(-1)).toBe("Type a podcast name to search…");
  });

  it("hands a pasted URL to the Add Source dialog", async () => {
    server.use(
      http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
      http.get("/api/settings/defaults", () => HttpResponse.json(mirrorDefaults())),
      http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 100, offset: 0 })),
      http.post("/api/probe", () => HttpResponse.json({ input_url: "x", candidates: [] })),
    );
    const user = userEvent.setup();
    const { history } = renderApp("/");
    await user.type(
      await screen.findByLabelText("Source URL"),
      "https://podcast.example/feed.xml{Enter}",
    );
    expect(await screen.findByRole("dialog", { name: "Choose a Source" })).toBeInTheDocument();
    expect(history.location.pathname).toBe("/");
    // The bar is cleared for the next Source.
    expect(screen.getByLabelText("Source URL")).toHaveValue("");
  });

  it("holds the Playlists menu next to Mirror without it submitting the bar", async () => {
    server.use(
      http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
      http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 100, offset: 0 })),
      http.get("/api/youtube/playlists", () =>
        HttpResponse.json({
          playlists: [
            {
              id: "PL1",
              title: "Talks",
              url: "https://www.youtube.com/playlist?list=PL1",
              item_count: 12,
              captured_feed_id: "mirror-talks",
            },
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    const { history } = renderApp("/");
    const field = await screen.findByLabelText("Source URL");
    const form = field.closest("form")!;
    // No visible "Feeds" heading any more: the bar comes first, the page heading is for screen
    // readers only.
    expect(screen.getByRole("heading", { name: "Feeds", level: 1 })).toHaveClass("sr-only");
    const playlists = within(form).getByRole("button", { name: "YouTube Playlists" });
    expect(playlists).toHaveAttribute("type", "button");
    expect(playlists).toHaveTextContent(/^Playlists$/);

    await user.type(field, "atp");
    await user.click(playlists);
    const menu = await screen.findByRole("menu");
    // Watch Later is always offered; a captured playlist links to its feed page.
    expect(within(menu).getByRole("menuitem", { name: /Watch Later/ })).toBeInTheDocument();
    expect(await within(menu).findByRole("menuitem", { name: /Talks/ })).toHaveAttribute(
      "href",
      "/feed/mirror-talks",
    );
    await user.keyboard("{Escape}");
    // Opening the menu submitted nothing: the text is still in the bar, no dialog opened.
    expect(field).toHaveValue("atp");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(history.location.pathname).toBe("/");
  });
});
