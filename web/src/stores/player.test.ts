import { beforeEach, describe, expect, it } from "vitest";

import { readListening, writeListening } from "./listening";
import {
  PLAYER_STORAGE_KEY,
  PLAYER_TRACK_STORAGE_KEY,
  chapterTarget,
  clampRate,
  formatRate,
  getPlayerState,
  play,
  reloadPlayerForTests,
  reportPlayback,
  resetPlayerForTests,
  seek,
  setChapters,
  setRate,
  setSleepTimer,
  stepRate,
  stop,
  tickSleepTimer,
  type PlayerTrack,
} from "./player";

const track: PlayerTrack = {
  itemId: "item-1",
  feedId: "mirror-1",
  title: "Episode one",
  feedTitle: "Example Podcast",
  url: "/feeds/mirror-1/media/item-1.m4a",
  mime: "audio/mp4",
  artworkUrl: null,
  durationSeconds: 3600,
  chaptersUrl: null,
};

const chapters = [
  { startTime: 0, title: "Intro" },
  { startTime: 600, title: "News" },
  { startTime: 1800, title: "Interview" },
];

beforeEach(() => {
  resetPlayerForTests();
});

describe("speed", () => {
  it("clamps to 0.5–3× on the 0.05 grid", () => {
    expect(clampRate(1.337)).toBe(1.35);
    expect(clampRate(0.1)).toBe(0.5);
    expect(clampRate(9)).toBe(3);
    expect(clampRate(Number.NaN)).toBe(1);
    expect(formatRate(1.35)).toBe("1.35");
    expect(formatRate(1.5)).toBe("1.5");
    expect(formatRate(2)).toBe("2");
    setRate(4);
    expect(getPlayerState().rate).toBe(3);
  });

  it("steps without wrapping around", () => {
    setRate(2.95);
    stepRate(0.1);
    expect(getPlayerState().rate).toBe(3);
    stepRate(0.1);
    expect(getPlayerState().rate).toBe(3);
    setRate(0.55);
    stepRate(-0.1);
    expect(getPlayerState().rate).toBe(0.5);
    setRate(1.35);
    stepRate(0.1);
    expect(getPlayerState().rate).toBe(1.45);
  });

  it("keeps every speed stored by earlier versions and resets one out of range", () => {
    for (const rate of [0.75, 1, 1.25, 1.5, 1.75, 2]) {
      window.localStorage.setItem(
        PLAYER_STORAGE_KEY,
        JSON.stringify({ rate, volume: 0.5, muted: true }),
      );
      reloadPlayerForTests();
      expect(getPlayerState()).toMatchObject({ rate, volume: 0.5, muted: true, shortcuts: true });
    }
    window.localStorage.setItem(PLAYER_STORAGE_KEY, JSON.stringify({ rate: 1.35 }));
    reloadPlayerForTests();
    expect(getPlayerState().rate).toBe(1.35);
    for (const rate of [5, 0.25, "2", null]) {
      window.localStorage.setItem(PLAYER_STORAGE_KEY, JSON.stringify({ rate, shortcuts: false }));
      reloadPlayerForTests();
      expect(getPlayerState()).toMatchObject({ rate: 1, shortcuts: false });
    }
    window.localStorage.setItem(PLAYER_STORAGE_KEY, "{not json");
    reloadPlayerForTests();
    expect(getPlayerState()).toMatchObject({ rate: 1, volume: 1, muted: false, shortcuts: true });
  });
});

describe("chapters", () => {
  it("goes to the next chapter, or restarts the current one before going back", () => {
    expect(chapterTarget([], 10, 1)).toBeNull();
    expect(chapterTarget(chapters, 10, 1)).toBe(600);
    expect(chapterTarget(chapters, 1900, 1)).toBeNull();
    expect(chapterTarget(chapters, 1900, -1)).toBe(1800);
    expect(chapterTarget(chapters, 1802, -1)).toBe(600);
    expect(chapterTarget(chapters, 2, -1)).toBe(0);
    expect(chapterTarget(chapters.slice(1), 100, -1)).toBe(0);
  });
});

describe("listening", () => {
  it("resumes, saves the position on a switch, and marks Finished on a stop near the end", () => {
    writeListening("item-1", { t: 600, d: 3600, at: Date.now(), finished: false });
    play(track);
    expect(getPlayerState()).toMatchObject({ playing: true, currentTime: 600 });
    expect(JSON.parse(window.localStorage.getItem(PLAYER_TRACK_STORAGE_KEY) ?? "null")).toEqual(
      track,
    );

    reportPlayback({ currentTime: 900 });
    play({ ...track, itemId: "item-2" });
    expect(readListening("item-1")).toMatchObject({ t: 900, d: 3600, finished: false });
    expect(getPlayerState().currentTime).toBe(0);

    seek(3580);
    stop();
    expect(readListening("item-2")).toMatchObject({ t: 3580, finished: true });
    expect(window.localStorage.getItem(PLAYER_TRACK_STORAGE_KEY)).toBeNull();
  });

  it("never marks Finished when the duration is unknown", () => {
    play({ ...track, durationSeconds: null });
    seek(5000);
    stop();
    expect(readListening("item-1")).toMatchObject({ t: 5000, d: null, finished: false });
  });
});

describe("sleep timer", () => {
  it("fades over the last 10 s of a minutes timer, then pauses", () => {
    play(track);
    setSleepTimer(5, 0);
    expect(tickSleepTimer(60_000)).toBe(1);
    expect(getPlayerState().sleep).toMatchObject({ kind: "minutes", leftMs: 240_000 });
    expect(tickSleepTimer(295_000)).toBeCloseTo(0.5);
    expect(getPlayerState().playing).toBe(true);
    // Run out: silent until the element has paused.
    expect(tickSleepTimer(300_000)).toBe(0);
    expect(getPlayerState()).toMatchObject({ playing: false, sleep: null });
  });

  it("keeps counting while paused by hand", () => {
    play(track);
    setSleepTimer(15, 0);
    reportPlayback({ playing: false });
    tickSleepTimer(10 * 60_000);
    expect(getPlayerState().sleep).toMatchObject({ kind: "minutes" });
    tickSleepTimer(15 * 60_000);
    expect(getPlayerState().sleep).toBeNull();
  });

  it("pauses at the end of the chapter playing when it was set", () => {
    play(track);
    setChapters(chapters);
    seek(700);
    setSleepTimer("chapter");
    expect(getPlayerState().sleep).toEqual({ kind: "chapter", endsAt: 1800 });
    reportPlayback({ currentTime: 1799 });
    tickSleepTimer(Date.now());
    expect(getPlayerState().playing).toBe(true);
    reportPlayback({ currentTime: 1800.2 });
    tickSleepTimer(Date.now());
    expect(getPlayerState()).toMatchObject({ playing: false, sleep: null });
  });
});
