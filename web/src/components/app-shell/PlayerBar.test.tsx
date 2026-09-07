import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlayerBar, parseChapters } from "./PlayerBar";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import {
  PLAYER_STORAGE_KEY,
  getPlayerState,
  play,
  resetPlayerForTests,
  type PlayerTrack,
} from "@/stores/player";

const track: PlayerTrack = {
  itemId: "item-1",
  feedId: "mirror-1",
  title: "Episode one",
  feedTitle: "Example Podcast",
  url: "http://localhost:8080/feeds/mirror-1/media/item-1.m4a",
  mime: "audio/mp4",
  artworkUrl: null,
  durationSeconds: 3600,
  chaptersUrl: "http://localhost:8080/feeds/mirror-1/assets/item-1.chapters.json",
};

describe("parseChapters", () => {
  it("reads Podcasting 2.0 chapters and sorts them", () => {
    expect(
      parseChapters({
        chapters: [
          { startTime: 600, title: "Second" },
          { startTime: 0, title: "Intro" },
          { startTime: -1, title: "bogus" },
          { title: "no time" },
          "junk",
        ],
      }),
    ).toEqual([
      { startTime: 0, title: "Intro" },
      { startTime: 600, title: "Second" },
    ]);
    expect(parseChapters(null)).toEqual([]);
    expect(parseChapters({ chapters: "x" })).toEqual([]);
  });
});

describe("PlayerBar", () => {
  beforeEach(() => {
    resetPlayerForTests();
    window.localStorage.removeItem(PLAYER_STORAGE_KEY);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
    server.use(
      http.get("http://localhost:8080/feeds/mirror-1/assets/item-1.chapters.json", () =>
        HttpResponse.json({
          chapters: [
            { startTime: 0, title: "Intro" },
            { startTime: 1800, title: "Halfway" },
          ],
        }),
      ),
    );
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("skips, seeks by keyboard, changes speed and volume, and shows chapters", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PlayerBar />);
    expect(screen.queryByRole("region", { name: "Player" })).not.toBeInTheDocument();
    play(track);
    const player = await screen.findByRole("region", { name: "Player" });
    expect(player).toHaveTextContent("Episode one");
    expect(within(player).getByRole("slider", { name: "Seek" })).toHaveAttribute("max", "3600");
    // Chapters arrive from the asset and mark the seek bar.
    await waitFor(() => expect(within(player).getAllByTestId("chapter-mark")).toHaveLength(2));
    expect(screen.getByTestId("player-chapter")).toHaveTextContent("Intro");

    await user.click(within(player).getByRole("button", { name: "Forward 30 seconds" }));
    expect(getPlayerState().currentTime).toBe(30);
    await user.click(within(player).getByRole("button", { name: "Back 15 seconds" }));
    expect(getPlayerState().currentTime).toBe(15);
    await user.keyboard("{ArrowRight}");
    expect(getPlayerState().currentTime).toBe(45);
    await user.keyboard(" ");
    expect(getPlayerState().playing).toBe(false);
    await user.keyboard(" ");
    expect(getPlayerState().playing).toBe(true);

    // Speed and volume are remembered.
    await user.click(within(player).getByRole("button", { name: "Playback speed" }));
    await user.click(await screen.findByRole("menuitem", { name: "1.5×" }));
    expect(getPlayerState().rate).toBe(1.5);
    await user.keyboard("]");
    expect(getPlayerState().rate).toBe(1.75);
    await user.click(within(player).getByRole("button", { name: "Mute" }));
    expect(getPlayerState().muted).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(PLAYER_STORAGE_KEY) ?? "{}")).toEqual({
      rate: 1.75,
      volume: 1,
      muted: true,
    });

    await user.click(within(player).getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Player" })).not.toBeInTheDocument(),
    );
  });

  it("leaves the keyboard alone while typing in a field", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <input aria-label="Search" />
        <PlayerBar />
      </>,
    );
    play(track);
    await screen.findByRole("region", { name: "Player" });
    await user.click(screen.getByRole("textbox", { name: "Search" }));
    await user.keyboard("{ArrowRight} m");
    expect(getPlayerState().currentTime).toBe(0);
    expect(getPlayerState().muted).toBe(false);
  });
});
