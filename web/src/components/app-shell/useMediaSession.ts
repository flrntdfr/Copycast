import { useEffect, type RefObject } from "react";

import { isAppleMobile } from "@/lib/platform";
import {
  SKIP_BACK_SECONDS,
  SKIP_FORWARD_SECONDS,
  pause,
  reportPlayback,
  resume,
  seek,
  skip,
  skipChapter,
  stop,
  type PlayerTrack,
} from "@/stores/player";

const ACTIONS: readonly MediaSessionAction[] = [
  "play",
  "pause",
  "stop",
  "seekbackward",
  "seekforward",
  "seekto",
  "previoustrack",
  "nexttrack",
];

/** The element events after which the lock screen's position is refreshed. */
const POSITION_EVENTS = ["loadedmetadata", "seeked", "ratechange", "play", "pause"] as const;

function supported(): boolean {
  return typeof navigator !== "undefined" && "mediaSession" in navigator;
}

/** Browsers throw on actions they do not know (an old Safari and "seekto"). */
function setHandler(action: MediaSessionAction, handler: MediaSessionActionHandler | null): void {
  try {
    navigator.mediaSession.setActionHandler(action, handler);
  } catch {
    // Unsupported action.
  }
}

function updatePositionState(audio: HTMLMediaElement): void {
  const { duration, currentTime: position, playbackRate } = audio;
  if (!Number.isFinite(duration) || duration <= 0) return;
  if (!(position >= 0 && position <= duration) || !(playbackRate > 0)) return;
  try {
    navigator.mediaSession.setPositionState({ duration, playbackRate, position });
  } catch {
    // An inconsistent state is refused; the next event sets it again.
  }
}

/**
 * Lock screen, headphone and Control Center controls for the player: metadata, play/pause,
 * stop, skip back 15 s / forward 30 s, scrubbing, and chapter previous/next when the Episode
 * has at least two chapters. Not on iOS and iPadOS, where track handlers replace the seek
 * buttons. Everything is cleared when the player stops or unmounts.
 */
export function useMediaSession(
  audioRef: RefObject<HTMLAudioElement | null>,
  {
    track,
    playing,
    chapterTitle,
    chapterCount,
  }: { track: PlayerTrack | null; playing: boolean; chapterTitle: string; chapterCount: number },
): void {
  useEffect(() => {
    if (!supported()) return;
    const session = navigator.mediaSession;
    if (!track || typeof MediaMetadata === "undefined") {
      session.metadata = null;
      return;
    }
    session.metadata = new MediaMetadata({
      title: track.title,
      artist: track.feedTitle,
      album: chapterTitle || track.feedTitle,
      artwork: track.artworkUrl ? [{ src: track.artworkUrl }] : [],
    });
  }, [track, chapterTitle]);

  useEffect(() => {
    if (!supported()) return;
    navigator.mediaSession.playbackState = track ? (playing ? "playing" : "paused") : "none";
  }, [track, playing]);

  const hasTrack = track !== null;
  const chapterHandlers = chapterCount >= 2 && !isAppleMobile();
  useEffect(() => {
    if (!supported() || !hasTrack) return;
    setHandler("play", () => resume());
    setHandler("pause", () => pause());
    setHandler("stop", () => stop());
    setHandler("seekbackward", (details) => skip(-(details.seekOffset ?? SKIP_BACK_SECONDS)));
    setHandler("seekforward", (details) => skip(details.seekOffset ?? SKIP_FORWARD_SECONDS));
    setHandler("seekto", (details) => {
      if (details.seekTime === undefined) return;
      const audio = audioRef.current;
      if (details.fastSeek && audio && "fastSeek" in audio) {
        audio.fastSeek(details.seekTime);
        reportPlayback({ currentTime: details.seekTime });
      } else {
        seek(details.seekTime);
      }
    });
    if (chapterHandlers) {
      setHandler("previoustrack", () => skipChapter(-1));
      setHandler("nexttrack", () => skipChapter(1));
    }
    return () => {
      for (const action of ACTIONS) setHandler(action, null);
    };
  }, [audioRef, hasTrack, chapterHandlers]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!supported() || !audio) return;
    const update = () => updatePositionState(audio);
    for (const name of POSITION_EVENTS) audio.addEventListener(name, update);
    return () => {
      for (const name of POSITION_EVENTS) audio.removeEventListener(name, update);
    };
  }, [audioRef]);
}
