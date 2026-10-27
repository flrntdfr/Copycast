import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { PLAYER_HEIGHT_VAR, PlayerBar, parseChapters } from "./PlayerBar";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { readListening, writeListening } from "@/stores/listening";
import {
  PLAYER_STORAGE_KEY,
  PLAYER_TRACK_STORAGE_KEY,
  getPlayerState,
  play,
  reloadPlayerForTests,
  reportPlayback,
  resetPlayerForTests,
  seek,
  setRate,
  setShortcuts,
  setSleepTimer,
  stop,
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
/** The same Episode without chapters, so nothing arrives after the test. */
const plain: PlayerTrack = { ...track, chaptersUrl: null };

function audioElement(): HTMLAudioElement {
  const audio = document.querySelector("audio");
  if (!audio) throw new Error("no <audio>");
  return audio;
}

function setDuration(audio: HTMLAudioElement, seconds: number): void {
  Object.defineProperty(audio, "duration", { value: seconds, configurable: true });
}

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

  it("also reads the raw yt-dlp list older archives hold", () => {
    expect(
      parseChapters([
        { start_time: 95.5, end_time: 300, title: "Topic" },
        { start_time: 0, end_time: 95.5, title: "Intro" },
        { start_time: "1", title: "bogus" },
      ]),
    ).toEqual([
      { startTime: 0, title: "Intro" },
      { startTime: 95.5, title: "Topic" },
    ]);
  });
});

describe("PlayerBar", () => {
  let playSpy: MockInstance<HTMLMediaElement["play"]>;
  beforeEach(() => {
    resetPlayerForTests();
    window.localStorage.removeItem(PLAYER_STORAGE_KEY);
    playSpy = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
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
    vi.unstubAllGlobals();
  });

  it("skips, seeks by keyboard, changes speed and volume, and shows chapters", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PlayerBar />);
    expect(screen.queryByRole("region", { name: "Player" })).not.toBeInTheDocument();
    act(() => play(track));
    const player = await screen.findByRole("region", { name: "Player" });
    expect(player).toHaveTextContent("Episode one");
    expect(within(player).getByRole("slider", { name: "Seek" })).toHaveAttribute("max", "3600");
    expect(player).toHaveTextContent("−1:00:00");
    // Chapters arrive from the asset and mark the seek bar.
    await waitFor(() => expect(within(player).getAllByTestId("chapter-mark")).toHaveLength(2));
    expect(screen.getByTestId("player-chapter")).toHaveTextContent("Intro");

    await user.click(within(player).getByRole("button", { name: "Forward 30 seconds" }));
    expect(getPlayerState().currentTime).toBe(30);
    await user.click(within(player).getByRole("button", { name: "Back 15 seconds" }));
    expect(getPlayerState().currentTime).toBe(15);
    await user.keyboard("{ArrowRight}");
    expect(getPlayerState().currentTime).toBe(45);
    // Space on the focused button is the button's; with nothing focused it toggles.
    act(() => (document.activeElement as HTMLElement).blur());
    await user.keyboard(" ");
    expect(getPlayerState().playing).toBe(false);
    await user.keyboard(" ");
    expect(getPlayerState().playing).toBe(true);

    // Speed: presets, the slider and −/+ in a popover; [ and ] step by 0.1.
    await user.click(within(player).getByRole("button", { name: "Playback speed, 1 times" }));
    const speed = await screen.findByRole("dialog");
    await user.click(within(speed).getByRole("radio", { name: "1.5×" }));
    expect(getPlayerState().rate).toBe(1.5);
    const slider = within(speed).getByRole("slider", { name: "Speed" });
    expect(slider).toHaveAttribute("aria-valuetext", "1.5 times");
    fireEvent.change(slider, { target: { value: "2.35" } });
    expect(getPlayerState().rate).toBe(2.35);
    await user.click(within(speed).getByRole("button", { name: "Faster" }));
    expect(getPlayerState().rate).toBe(2.4);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(
      within(player).getByRole("button", { name: "Playback speed, 2.4 times" }),
    ).toHaveTextContent("2.4×");
    await user.keyboard("]");
    expect(getPlayerState().rate).toBe(2.5);
    // "[[" is how user-event types one "[".
    await user.keyboard("[[[[");
    expect(getPlayerState().rate).toBe(2.3);

    await user.click(within(player).getByRole("button", { name: "Mute" }));
    expect(getPlayerState().muted).toBe(true);
    expect(JSON.parse(window.localStorage.getItem(PLAYER_STORAGE_KEY) ?? "{}")).toEqual({
      rate: 2.3,
      volume: 1,
      muted: true,
      shortcuts: true,
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
    act(() => play(plain));
    await screen.findByRole("region", { name: "Player" });
    await user.click(screen.getByRole("textbox", { name: "Search" }));
    await user.keyboard("{ArrowRight} m");
    expect(getPlayerState().currentTime).toBe(0);
    expect(getPlayerState().muted).toBe(false);
    expect(getPlayerState().playing).toBe(true);
  });

  it("leaves Space to focused controls and ↑/↓ to the page outside the player", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <button type="button">Other</button>
        <input type="checkbox" aria-label="Pick" />
        <PlayerBar />
      </>,
    );
    act(() => play(plain));
    const player = await screen.findByRole("region", { name: "Player" });

    await user.click(screen.getByRole("button", { name: "Other" }));
    await user.keyboard(" ");
    expect(getPlayerState().playing).toBe(true);
    const checkbox = screen.getByRole("checkbox", { name: "Pick" });
    checkbox.focus();
    await user.keyboard(" ");
    expect(checkbox).toBeChecked();
    expect(getPlayerState().playing).toBe(true);

    act(() => checkbox.blur());
    await user.keyboard("{ArrowDown}{ArrowUp}");
    expect(getPlayerState().volume).toBe(1);
    within(player).getByRole("button", { name: "Pause" }).focus();
    await user.keyboard("{ArrowDown}");
    expect(getPlayerState().volume).toBeCloseTo(0.9);

    // The switch in the shortcuts dialog turns the single keys off.
    act(() => setShortcuts(false));
    act(() => (document.activeElement as HTMLElement).blur());
    await user.keyboard("{ArrowRight} m");
    expect(getPlayerState()).toMatchObject({ currentTime: 0, muted: false, playing: true });
  });

  it("keeps focus order in the order the controls are drawn", async () => {
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    const player = await screen.findByRole("region", { name: "Player" });
    // Phones draw them in one row, left to right; from sm the transport sits between them.
    const names = within(player)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([
      "Sleep timer",
      "Back 15 seconds",
      "Pause",
      "Forward 30 seconds",
      "Playback speed, 1 times",
      "Mute",
      "Stop",
    ]);
  });

  it("names the keys in tooltips only while single-key shortcuts are on", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    const player = await screen.findByRole("region", { name: "Player" });
    const tips = () => screen.getAllByRole("tooltip").map((tip) => tip.textContent);

    await user.hover(within(player).getByRole("button", { name: "Forward 30 seconds" }));
    await waitFor(() => expect(tips()).toContain("Forward 30 s (→)"));
    act(() => setShortcuts(false));
    await waitFor(() => expect(tips()).toContain("Forward 30 s"));
    await user.unhover(within(player).getByRole("button", { name: "Forward 30 seconds" }));
    act(() =>
      within(player)
        .getByRole("button", { name: /Playback speed/ })
        .focus(),
    );
    await waitFor(() => expect(tips()).toContain("Speed: scroll"));
    act(() => setShortcuts(true));
    await waitFor(() => expect(tips()).toContain("Speed: scroll, or [ and ]"));
  });

  it("changes speed with the mouse wheel over the speed chip", async () => {
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    const player = await screen.findByRole("region", { name: "Player" });
    const chip = within(player).getByRole("button", { name: "Playback speed, 1 times" });
    // Not passive: the page does not scroll underneath.
    expect(fireEvent.wheel(chip, { deltaY: -100 })).toBe(false);
    expect(getPlayerState().rate).toBe(1.05);
    // Trackpads send small deltas; 40 px make one step.
    fireEvent.wheel(chip, { deltaY: 20 });
    expect(getPlayerState().rate).toBe(1.05);
    fireEvent.wheel(chip, { deltaY: 20 });
    expect(getPlayerState().rate).toBe(1);
    expect(chip).toHaveAccessibleName("Playback speed, 1 times");
  });

  it("keeps the speed across the load algorithm and track switches", () => {
    setRate(1.5);
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    const audio = audioElement();
    expect(audio.defaultPlaybackRate).toBe(1.5);
    expect(audio.playbackRate).toBe(1.5);

    // Loading resets playbackRate to defaultPlaybackRate; metadata puts the speed back.
    audio.defaultPlaybackRate = 1;
    audio.playbackRate = 1;
    fireEvent.loadedMetadata(audio);
    expect(audio.playbackRate).toBe(1.5);
    expect(audio.defaultPlaybackRate).toBe(1.5);
    expect(audio.preservesPitch).toBe(true);

    audio.defaultPlaybackRate = 1;
    audio.playbackRate = 1;
    const next = { ...plain, itemId: "item-2", url: "/feeds/mirror-1/media/item-2.m4a" };
    act(() => play(next));
    expect(audio.src).toBe(new URL(next.url, window.location.href).href);
    expect(audio.defaultPlaybackRate).toBe(1.5);
    expect(audio.playbackRate).toBe(1.5);

    // A relative media URL is not reloaded by pause and resume.
    const src = vi.spyOn(HTMLMediaElement.prototype, "src", "set");
    act(() => play(next));
    act(() => play(next));
    expect(src).not.toHaveBeenCalled();
  });

  it("resumes where the Episode was left, rewound by the time away", () => {
    writeListening("item-1", {
      t: 600,
      d: 3600,
      at: Date.now() - 2 * 3_600_000,
      finished: false,
    });
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    expect(getPlayerState().currentTime).toBe(590);
    const audio = audioElement();
    expect(audio.currentTime).toBe(590);

    // Without metadata the element reports 0 at first; that must not move the start.
    audio.currentTime = 0;
    fireEvent.timeUpdate(audio);
    expect(getPlayerState().currentTime).toBe(590);
    setDuration(audio, 3600);
    fireEvent.loadedMetadata(audio);
    expect(audio.currentTime).toBe(590);
    fireEvent.seeked(audio);
    // Nothing heard yet: the stored position keeps its own value, not the rewound one.
    expect(readListening("item-1")).toMatchObject({ t: 600, d: 3600, finished: false });

    audio.currentTime = 700;
    fireEvent.timeUpdate(audio);
    expect(getPlayerState().currentTime).toBe(700);
    fireEvent.pause(audio);
    expect(readListening("item-1")).toMatchObject({ t: 700 });
  });

  it("brings the last track back paused after a reload, and forgets it when it fails", () => {
    window.localStorage.setItem(PLAYER_TRACK_STORAGE_KEY, JSON.stringify(plain));
    writeListening("item-1", { t: 1200, d: 3600, at: Date.now() - 30_000, finished: false });
    reloadPlayerForTests();
    renderWithProviders(<PlayerBar />);
    const player = screen.getByRole("region", { name: "Player" });
    expect(within(player).getByRole("button", { name: "Play" })).toBeInTheDocument();
    expect(getPlayerState()).toMatchObject({ playing: false, currentTime: 1200 });
    expect(playSpy).not.toHaveBeenCalled();

    fireEvent.error(audioElement());
    expect(window.localStorage.getItem(PLAYER_TRACK_STORAGE_KEY)).toBeNull();
    expect(player).toHaveTextContent("The media could not be played");
  });

  it("does not rewind the stored position again on reloads where nothing plays", () => {
    window.localStorage.setItem(PLAYER_TRACK_STORAGE_KEY, JSON.stringify(plain));
    writeListening("item-1", {
      t: 3600 - 600,
      d: 3600,
      at: Date.now() - 2 * 86_400_000,
      finished: false,
    });
    reloadPlayerForTests();
    renderWithProviders(<PlayerBar />);
    expect(getPlayerState().currentTime).toBe(2980);

    // Hidden, closed, stopped: the stored position is still where it was left.
    act(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("pagehide"));
    });
    Reflect.deleteProperty(document, "visibilityState");
    expect(readListening("item-1")).toMatchObject({ t: 3000 });
    act(() => stop());
    expect(readListening("item-1")).toMatchObject({ t: 3000, finished: false });
  });

  it("marks an Episode Finished at the end, and replays it from the start", () => {
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    act(() => seek(3599));
    fireEvent.ended(audioElement());
    expect(readListening("item-1")).toMatchObject({ t: 3599, finished: true });
    expect(getPlayerState().playing).toBe(false);

    act(() => stop());
    act(() => play(plain));
    expect(getPlayerState().currentTime).toBe(0);
    // Finished until it passes 10 s again.
    fireEvent.pause(audioElement());
    expect(readListening("item-1")?.finished).toBe(true);
    act(() => seek(120));
    fireEvent.pause(audioElement());
    expect(readListening("item-1")).toMatchObject({ t: 120, finished: false });
  });

  it("pauses when the sleep timer runs out, fading the volume first", () => {
    vi.useFakeTimers();
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    act(() => setSleepTimer(5));
    const player = screen.getByRole("region", { name: "Player" });
    expect(
      within(player).getByRole("button", { name: "Sleep timer, 5 minutes left" }),
    ).toHaveTextContent("5m");
    const audio = audioElement();

    act(() => {
      vi.advanceTimersByTime(295_000);
    });
    expect(getPlayerState().playing).toBe(true);
    expect(audio.volume).toBeCloseTo(0.5);
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(getPlayerState()).toMatchObject({ playing: false, sleep: null });
    expect(audio.volume).toBe(1);
    expect(within(player).getByRole("button", { name: "Sleep timer" })).toBeInTheDocument();
  });

  it("pauses the element before the volume comes back when the sleep timer runs out", () => {
    vi.useFakeTimers();
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    act(() => setSleepTimer(5));
    const audio = audioElement();
    Object.defineProperty(audio, "paused", { value: false, configurable: true });
    const events: string[] = [];
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {
      events.push("pause");
      Object.defineProperty(audio, "paused", { value: true, configurable: true });
    });
    const volume = vi.spyOn(HTMLMediaElement.prototype, "volume", "set");
    volume.mockImplementation((value: number) => events.push(`volume ${value}`));

    act(() => {
      vi.advanceTimersByTime(299_000);
    });
    events.length = 0;
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(getPlayerState()).toMatchObject({ playing: false, sleep: null });
    // Silent, then paused, and only then the stored volume again: no burst at full volume.
    expect(events.slice(0, 2)).toEqual(["pause", "volume 0"]);
    expect(events.indexOf("volume 1")).toBeGreaterThan(events.indexOf("pause"));
  });

  it("never pauses a playing element while the sleep timer is still running", () => {
    vi.useFakeTimers();
    renderWithProviders(<PlayerBar />);
    act(() => play(plain));
    act(() => setSleepTimer(15));
    const audio = audioElement();
    // The element plays but the store has not caught up yet (a play still being reported).
    Object.defineProperty(audio, "paused", { value: false, configurable: true });
    const paused = vi.spyOn(HTMLMediaElement.prototype, "pause");
    paused.mockClear();
    act(() => reportPlayback({ playing: false }));
    paused.mockClear();
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    fireEvent.timeUpdate(audio);
    expect(paused).not.toHaveBeenCalled();
    expect(getPlayerState().sleep).toMatchObject({ kind: "minutes", minutes: 15 });
  });

  it("lists chapters, jumps to one, and steps with Shift+arrows", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PlayerBar />);
    act(() => play(track));
    const player = await screen.findByRole("region", { name: "Player" });
    await waitFor(() => expect(within(player).getAllByTestId("chapter-mark")).toHaveLength(2));
    for (const mark of within(player).getAllByTestId("chapter-mark"))
      expect(mark).toHaveAttribute("aria-hidden", "true");

    await user.click(screen.getByTestId("player-chapter"));
    await user.click(await screen.findByRole("button", { name: /Halfway/ }));
    expect(getPlayerState().currentTime).toBe(1800);
    await waitFor(() => expect(screen.getByTestId("player-chapter")).toHaveTextContent("Halfway"));

    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(getPlayerState().currentTime).toBe(0);
    await user.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(getPlayerState().currentTime).toBe(1800);
    act(() => seek(1810));
    await user.keyboard("{Shift>}{ArrowLeft}{/Shift}");
    expect(getPlayerState().currentTime).toBe(1800);
  });

  it("registers lock-screen controls and clears them on stop", async () => {
    const handlers = new Map<string, MediaSessionActionHandler>();
    const session = {
      metadata: null as MediaMetadata | null,
      playbackState: "none" as MediaSessionPlaybackState,
      setActionHandler: vi.fn(
        (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
          if (handler) handlers.set(action, handler);
          else handlers.delete(action);
        },
      ),
      setPositionState: vi.fn(),
    };
    Object.defineProperty(window.navigator, "mediaSession", { value: session, configurable: true });
    vi.stubGlobal(
      "MediaMetadata",
      class {
        constructor(init: MediaMetadataInit) {
          Object.assign(this, init);
        }
      },
    );
    try {
      renderWithProviders(<PlayerBar />);
      act(() => play(track));
      // Chapter previous/next once there are two chapters (and this is not iOS).
      await waitFor(() => expect(handlers.has("nexttrack")).toBe(true));
      expect([...handlers.keys()]).toEqual(
        expect.arrayContaining(["play", "pause", "stop", "seekbackward", "seekforward", "seekto"]),
      );
      expect(session.metadata).toMatchObject({
        title: "Episode one",
        artist: "Example Podcast",
        album: "Intro",
      });
      expect(session.playbackState).toBe("playing");

      act(() => handlers.get("seekforward")?.({ action: "seekforward" }));
      expect(getPlayerState().currentTime).toBe(30);
      act(() => handlers.get("nexttrack")?.({ action: "nexttrack" }));
      expect(getPlayerState().currentTime).toBe(1800);
      act(() => handlers.get("pause")?.({ action: "pause" }));
      expect(session.playbackState).toBe("paused");

      const audio = audioElement();
      setDuration(audio, 3600);
      fireEvent.loadedMetadata(audio);
      expect(session.setPositionState).toHaveBeenLastCalledWith({
        duration: 3600,
        playbackRate: 1,
        position: 1800,
      });

      act(() => stop());
      expect(handlers.size).toBe(0);
      expect(session.playbackState).toBe("none");
      expect(session.metadata).toBeNull();
    } finally {
      Reflect.deleteProperty(window.navigator, "mediaSession");
    }
  });

  it("publishes its height as --player-h and clears it", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 375,
      bottom: 79.4,
      width: 375,
      height: 79.4,
      toJSON: () => ({}),
    });
    const root = document.documentElement;
    const { unmount } = renderWithProviders(<PlayerBar />);
    expect(root.style.getPropertyValue(PLAYER_HEIGHT_VAR)).toBe("0px");
    act(() => play(plain));
    expect(root.style.getPropertyValue(PLAYER_HEIGHT_VAR)).toBe("80px");
    act(() => stop());
    expect(root.style.getPropertyValue(PLAYER_HEIGHT_VAR)).toBe("0px");
    unmount();
    expect(root.style.getPropertyValue(PLAYER_HEIGHT_VAR)).toBe("");
  });
});
