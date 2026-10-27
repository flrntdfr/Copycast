import { Pause, Play, RotateCcw, RotateCw, Volume2, VolumeX, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ChapterMenu } from "./ChapterMenu";
import { SleepTimerControl } from "./SleepTimerControl";
import { SpeedControl } from "./SpeedControl";
import { useMediaSession } from "./useMediaSession";
import { Artwork } from "@/components/common/Artwork";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDuration } from "@/lib/format";
import { ownsKey } from "@/lib/keyboard";
import { isVolumeWritable } from "@/lib/platform";
import {
  RATE_KEY_STEP,
  SKIP_BACK_SECONDS,
  SKIP_FORWARD_SECONDS,
  chapterAt,
  failPlayback,
  finishTrack,
  getPlayerState,
  pause,
  reportPlayback,
  resume,
  saveListening,
  seek,
  setChapters,
  setVolume,
  skip,
  skipChapter,
  stepRate,
  stop,
  tickSleepTimer,
  toggleMute,
  togglePlay,
  usePlayer,
  type Chapter,
} from "@/stores/player";
import { cn } from "@/lib/utils";

/** The bar's height on `<html>`, for what must stay clear of it (page, SelectionBar, toasts). */
export const PLAYER_HEIGHT_VAR = "--player-h";

/** Positions this close count as the same (timeupdate granularity, seek slop). */
const POSITION_SLOP_SECONDS = 1.5;

/**
 * Podcasting 2.0 chapters, `{ "chapters": [{ "startTime": 0, "title": "Intro" }] }`, or the raw
 * yt-dlp list `[{ "start_time": 0, "title": "Intro" }]` that older archives still hold.
 */
export function parseChapters(data: unknown): Chapter[] {
  const list = Array.isArray(data)
    ? data
    : data && typeof data === "object"
      ? (data as { chapters?: unknown }).chapters
      : null;
  if (!Array.isArray(list)) return [];
  const chapters: Chapter[] = [];
  for (const entry of list as unknown[]) {
    if (!entry || typeof entry !== "object") continue;
    const { startTime, start_time, title } = entry as {
      startTime?: unknown;
      start_time?: unknown;
      title?: unknown;
    };
    const start = startTime ?? start_time;
    if (typeof start !== "number" || !Number.isFinite(start) || start < 0) continue;
    chapters.push({ startTime: start, title: typeof title === "string" ? title : "" });
  }
  return chapters.sort((a, b) => a.startTime - b.startTime);
}

/** `audio.src` reads back absolute; compare like with like so pause/resume never reloads. */
function absoluteUrl(url: string): string {
  try {
    return new URL(url, window.location.href).href;
  } catch {
    return url;
  }
}

/**
 * Setting `src` runs the load algorithm, which resets `playbackRate` to `defaultPlaybackRate`:
 * set both, and keep the pitch at speed.
 */
function applyRate(audio: HTMLMediaElement, rate: number): void {
  audio.defaultPlaybackRate = rate;
  audio.playbackRate = rate;
  audio.preservesPitch = true;
}

/**
 * Advance the sleep timer and fade the element where its volume is writable. When the timer
 * runs out the element pauses here, before the stored volume comes back, so the fade never
 * ends on a burst at full volume.
 */
function runSleepTimer(audio: HTMLMediaElement | null): void {
  const factor = tickSleepTimer(Date.now());
  if (!audio) return;
  // Only a timer that has just run out pauses here; any other gap between the store and
  // the element (a play that has not reported yet) is the main effect's business.
  if (factor === 0 && !audio.paused) audio.pause();
  if (isVolumeWritable()) audio.volume = getPlayerState().volume * factor;
}

function SkipButton({
  seconds,
  keyHint,
  className,
}: {
  seconds: number;
  /** Name the arrow key in the tooltip, while the single-key shortcuts are on. */
  keyHint: boolean;
  className?: string;
}) {
  const back = seconds < 0;
  const amount = Math.abs(seconds);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`${back ? "Back" : "Forward"} ${amount} seconds`}
          onClick={() => skip(seconds)}
          className={cn("relative size-10 sm:size-9", className)}
        >
          {back ? (
            <RotateCcw className="size-5 sm:size-4" />
          ) : (
            <RotateCw className="size-5 sm:size-4" />
          )}
          <span
            aria-hidden
            className="absolute inset-0 flex items-center justify-center text-[9px] font-semibold"
          >
            {amount}
          </span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {back ? "Back" : "Forward"} {amount} s{keyHint ? ` (${back ? "←" : "→"})` : ""}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The one `<audio preload="none">` element in the app, driven by the player store: transport,
 * seek bar with chapters, speed, sleep timer, volume, keyboard shortcuts, lock-screen controls,
 * and the listening position saved as it plays.
 */
export function PlayerBar() {
  const state = usePlayer();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const regionRef = useRef<HTMLDivElement | null>(null);
  // Where the element must start. Until it gets there (preload is "none", so it has no
  // metadata yet) its early events report 0, which must not overwrite the position.
  const pendingStart = useRef<number | null>(null);
  const [scrubbing, setScrubbing] = useState<number | null>(null);
  const hasTrack = state.track !== null;

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!state.track) {
      pendingStart.current = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      return;
    }
    const url = absoluteUrl(state.track.url);
    if (audio.src !== url) {
      const { rate, currentTime: start } = getPlayerState();
      audio.src = url;
      applyRate(audio, rate);
      pendingStart.current = start > 0 ? start : null;
      if (start > 0) audio.currentTime = start;
    }
    if (state.playing) {
      void audio.play().catch((error: unknown) => {
        // Switching track aborts the pending play(), and a media error has its own event.
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (audio.error) return;
        reportPlayback({
          playing: false,
          error: error instanceof Error ? error.message : "Playback failed",
        });
      });
    } else {
      audio.pause();
    }
  }, [state.track, state.playing]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !state.track) return;
    if (Math.abs(audio.currentTime - state.currentTime) > POSITION_SLOP_SECONDS) {
      audio.currentTime = state.currentTime;
      if (audio.readyState < HTMLMediaElement.HAVE_METADATA)
        pendingStart.current = state.currentTime;
    }
  }, [state.currentTime, state.track]);

  useEffect(() => {
    const audio = audioRef.current;
    if (audio) applyRate(audio, state.rate);
  }, [state.rate]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.volume = state.volume;
    audio.muted = state.muted;
  }, [state.volume, state.muted]);

  // Chapters come from the Episode's chapters asset, when it has one.
  const chaptersUrl = state.track?.chaptersUrl ?? null;
  useEffect(() => {
    if (!chaptersUrl) return;
    const controller = new AbortController();
    fetch(chaptersUrl, { signal: controller.signal, credentials: "same-origin" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data: unknown) => {
        if (!controller.signal.aborted) setChapters(parseChapters(data));
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [chaptersUrl]);

  // The position is saved when the page goes away (tab hidden, closed or navigated).
  useEffect(() => {
    if (!hasTrack) return;
    const onVisibility = () => {
      if (document.visibilityState === "hidden") saveListening();
    };
    const onPageHide = () => saveListening();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [hasTrack]);

  // The sleep timer runs on the wall clock even while paused; timeupdate checks it too.
  const sleepActive = state.sleep !== null;
  useEffect(() => {
    if (!sleepActive) return;
    const audio = audioRef.current;
    runSleepTimer(audio);
    const timer = window.setInterval(() => runSleepTimer(audio), 1000);
    return () => {
      window.clearInterval(timer);
      // Cancelled or run out: the element gets the stored volume back after the fade.
      if (audio) audio.volume = getPlayerState().volume;
    };
  }, [sleepActive]);

  // Keyboard: Space, arrows, m, [ and ], unless the focused element uses the key itself.
  // ↑/↓ change the volume only inside the player; elsewhere they scroll the page.
  const shortcuts = state.shortcuts;
  useEffect(() => {
    if (!hasTrack || !shortcuts) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (ownsKey(event.target, event.key)) return;
      if (document.querySelector("[role=dialog], [role=alertdialog]")) return;
      switch (event.key) {
        case " ":
          event.preventDefault();
          togglePlay();
          break;
        case "ArrowLeft":
        case "ArrowRight": {
          const forward = event.key === "ArrowRight";
          if (event.shiftKey) {
            if (skipChapter(forward ? 1 : -1)) event.preventDefault();
          } else {
            event.preventDefault();
            skip(forward ? SKIP_FORWARD_SECONDS : -SKIP_BACK_SECONDS);
          }
          break;
        }
        case "ArrowUp":
        case "ArrowDown": {
          const inPlayer =
            event.target instanceof Node && !!regionRef.current?.contains(event.target);
          if (!inPlayer) break;
          event.preventDefault();
          setVolume(getPlayerState().volume + (event.key === "ArrowUp" ? 0.1 : -0.1));
          break;
        }
        case "m":
          toggleMute();
          break;
        case "]":
          stepRate(RATE_KEY_STEP);
          break;
        case "[":
          stepRate(-RATE_KEY_STEP);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasTrack, shortcuts]);

  // --player-h follows the bar's height (it wraps on phones and grows with the safe area).
  useLayoutEffect(() => {
    const root = document.documentElement;
    const region = regionRef.current;
    if (!region) {
      root.style.setProperty(PLAYER_HEIGHT_VAR, "0px");
      return;
    }
    const measure = () =>
      root.style.setProperty(
        PLAYER_HEIGHT_VAR,
        `${Math.ceil(region.getBoundingClientRect().height)}px`,
      );
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(region);
    return () => observer.disconnect();
  }, [hasTrack]);
  useEffect(
    () => () => {
      document.documentElement.style.removeProperty(PLAYER_HEIGHT_VAR);
    },
    [],
  );

  const track = state.track;
  const duration = state.duration || track?.durationSeconds || 0;
  const position = scrubbing ?? state.currentTime;
  const chapter = chapterAt(state.chapters, position);
  const hasChapters = state.chapters.length > 0;

  useMediaSession(audioRef, {
    track,
    playing: state.playing,
    chapterTitle: chapter?.title ?? "",
    chapterCount: state.chapters.length,
  });

  return (
    <>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- podcast audio; transcripts are served as feed assets */}
      <audio
        ref={audioRef}
        preload="none"
        onLoadedMetadata={(event) => {
          const audio = event.currentTarget;
          applyRate(audio, getPlayerState().rate);
          const known = Number.isFinite(audio.duration) && audio.duration > 0;
          if (known) reportPlayback({ duration: audio.duration });
          const start = pendingStart.current;
          if (start === null) return;
          if (known && start >= audio.duration) pendingStart.current = null;
          else audio.currentTime = start;
        }}
        onDurationChange={(event) => {
          const { duration: length } = event.currentTarget;
          if (Number.isFinite(length) && length > 0) reportPlayback({ duration: length });
        }}
        onTimeUpdate={(event) => {
          const time = event.currentTarget.currentTime;
          const start = pendingStart.current;
          if (start !== null) {
            if (Math.abs(time - start) > POSITION_SLOP_SECONDS) return;
            pendingStart.current = null;
          }
          reportPlayback({ currentTime: time });
          if (getPlayerState().sleep) runSleepTimer(event.currentTarget);
        }}
        onSeeked={(event) => {
          // A finished seek is where the element really is, whatever was pending.
          pendingStart.current = null;
          reportPlayback({ currentTime: event.currentTarget.currentTime });
          saveListening();
        }}
        onEnded={() => finishTrack()}
        onPause={(event) => {
          // A late event from before a newer play() must not pause the new track.
          if (!event.currentTarget.paused) return;
          reportPlayback({ playing: false });
          saveListening();
        }}
        onPlay={(event) => {
          if (event.currentTarget.paused) return;
          reportPlayback({ playing: true, error: null });
        }}
        onError={() => failPlayback("The media could not be played")}
      />
      {track ? (
        <div
          ref={regionRef}
          className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80"
          role="region"
          aria-label="Player"
        >
          <div className="mx-auto max-w-6xl pt-2 pr-[max(1rem,env(safe-area-inset-right))] pb-[max(0.5rem,env(safe-area-inset-bottom))] pl-[max(1rem,env(safe-area-inset-left))]">
            {/* Phones: now playing, then a row of six controls. From sm: one row with the
                transport centred between the sleep timer and the speed. The groups are
                `contents` on phones so their buttons take grid cells of their own, in DOM
                order: focus and reading order follow what is drawn (WCAG 2.4.3). */}
            <div className="grid grid-cols-6 items-center gap-x-1 gap-y-1 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:gap-x-3">
              <div className="col-span-6 flex min-w-0 items-center gap-3 sm:col-span-1">
                <Artwork src={track.artworkUrl} size={40} />
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium" title={track.title}>
                    {track.title}
                  </div>
                  {state.error ? (
                    <div className="truncate text-xs text-destructive" role="alert">
                      {state.error}
                    </div>
                  ) : (
                    <div className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                      <span className="truncate">{track.feedTitle}</span>
                      {hasChapters ? (
                        <>
                          <span aria-hidden>·</span>
                          <ChapterMenu
                            chapters={state.chapters}
                            current={chapter}
                            className="max-w-[60%] shrink-0"
                          />
                        </>
                      ) : null}
                    </div>
                  )}
                </div>
              </div>
              <div className="contents sm:col-start-2 sm:flex sm:items-center sm:gap-1">
                <SleepTimerControl
                  sleep={state.sleep}
                  hasChapters={hasChapters}
                  className="h-10 justify-self-center sm:h-8 sm:w-16"
                />
                <SkipButton
                  seconds={-SKIP_BACK_SECONDS}
                  keyHint={shortcuts}
                  className="justify-self-center"
                />
                <Button
                  variant="default"
                  size="icon"
                  aria-label={state.playing ? "Pause" : "Play"}
                  onClick={() => (state.playing ? pause() : resume())}
                  className="size-11 justify-self-center rounded-full sm:size-10"
                >
                  {state.playing ? (
                    <Pause className="size-5 fill-current" />
                  ) : (
                    <Play className="size-5 translate-x-px fill-current" />
                  )}
                </Button>
                <SkipButton
                  seconds={SKIP_FORWARD_SECONDS}
                  keyHint={shortcuts}
                  className="justify-self-center"
                />
                <SpeedControl
                  rate={state.rate}
                  keyHint={shortcuts}
                  className="h-10 justify-self-center sm:h-8 sm:w-16"
                />
              </div>
              <div className="contents sm:col-start-3 sm:flex sm:items-center sm:justify-end sm:gap-1">
                <div className="hidden items-center gap-1 sm:flex">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={state.muted ? "Unmute" : "Mute"}
                    aria-pressed={state.muted}
                    onClick={toggleMute}
                  >
                    {state.muted || state.volume === 0 ? <VolumeX /> : <Volume2 />}
                  </Button>
                  {/* iOS ignores the element volume; the hardware buttons own it. */}
                  {isVolumeWritable() ? (
                    <input
                      type="range"
                      aria-label="Volume"
                      min={0}
                      max={1}
                      step={0.05}
                      value={state.muted ? 0 : state.volume}
                      onChange={(event) => setVolume(Number(event.target.value))}
                      className="hidden h-1.5 w-20 cursor-pointer accent-primary md:block"
                    />
                  ) : null}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Stop"
                  onClick={stop}
                  className="size-10 justify-self-center sm:size-9"
                >
                  <X className="size-5 sm:size-4" />
                </Button>
              </div>
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
              <span className="w-14 shrink-0 text-right">{formatDuration(position)}</span>
              <div className="relative min-w-0 flex-1">
                <input
                  type="range"
                  aria-label="Seek"
                  aria-valuetext={formatDuration(position)}
                  min={0}
                  max={duration > 0 ? duration : 1}
                  step={1}
                  value={Math.min(position, duration > 0 ? duration : 1)}
                  disabled={duration <= 0}
                  onChange={(event) => setScrubbing(Number(event.target.value))}
                  onPointerUp={() => {
                    if (scrubbing !== null) seek(scrubbing);
                    setScrubbing(null);
                  }}
                  onKeyUp={() => {
                    if (scrubbing !== null) seek(scrubbing);
                    setScrubbing(null);
                  }}
                  className="block h-1.5 w-full cursor-pointer accent-primary"
                />
                {duration > 0
                  ? state.chapters.map((mark) => (
                      <span
                        key={mark.startTime}
                        aria-hidden
                        data-testid="chapter-mark"
                        className="pointer-events-none absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 bg-foreground/50"
                        style={{
                          left: `${(Math.min(mark.startTime, duration) / duration) * 100}%`,
                        }}
                      />
                    ))
                  : null}
              </div>
              <span className="w-14 shrink-0">
                {duration > 0 ? `−${formatDuration(Math.max(0, duration - position))}` : ""}
              </span>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
