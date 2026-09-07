import { Gauge, Pause, Play, RotateCcw, RotateCw, Square, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Artwork } from "@/components/common/Artwork";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatDuration } from "@/lib/format";
import {
  RATES,
  SKIP_BACK_SECONDS,
  SKIP_FORWARD_SECONDS,
  chapterAt,
  cycleRate,
  pause,
  reportPlayback,
  resume,
  seek,
  setChapters,
  setRate,
  setVolume,
  skip,
  stop,
  toggleMute,
  togglePlay,
  usePlayer,
  type Chapter,
} from "@/stores/player";
import { cn } from "@/lib/utils";

/** Podcasting 2.0 chapters: `{ "chapters": [{ "startTime": 0, "title": "Intro" }] }`. */
export function parseChapters(data: unknown): Chapter[] {
  if (!data || typeof data !== "object") return [];
  const list = (data as { chapters?: unknown }).chapters;
  if (!Array.isArray(list)) return [];
  const chapters: Chapter[] = [];
  for (const entry of list as unknown[]) {
    if (!entry || typeof entry !== "object") continue;
    const { startTime, title } = entry as { startTime?: unknown; title?: unknown };
    if (typeof startTime !== "number" || !Number.isFinite(startTime) || startTime < 0) continue;
    chapters.push({ startTime, title: typeof title === "string" ? title : "" });
  }
  return chapters.sort((a, b) => a.startTime - b.startTime);
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/**
 * The one `<audio preload="none">` element in the app, driven by the player store:
 * seek bar with chapter marks, skip back and forward, speed, volume, keyboard shortcuts.
 */
export function PlayerBar() {
  const state = usePlayer();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [scrubbing, setScrubbing] = useState<number | null>(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!state.track) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      return;
    }
    if (audio.src !== state.track.url) {
      audio.src = state.track.url;
    }
    if (state.playing) {
      void audio.play().catch((error: unknown) => {
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
    if (Math.abs(audio.currentTime - state.currentTime) > 1.5)
      audio.currentTime = state.currentTime;
  }, [state.currentTime, state.track]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.playbackRate = state.rate;
    audio.volume = state.volume;
    audio.muted = state.muted;
  }, [state.rate, state.volume, state.muted]);

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

  // Keyboard: space, arrows, m, [ and ], unless typing somewhere.
  const hasTrack = state.track !== null;
  useEffect(() => {
    if (!hasTrack) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      if (document.querySelector("[role=dialog], [role=alertdialog]")) return;
      switch (event.key) {
        case " ":
          event.preventDefault();
          togglePlay();
          break;
        case "ArrowLeft":
          event.preventDefault();
          skip(-SKIP_BACK_SECONDS);
          break;
        case "ArrowRight":
          event.preventDefault();
          skip(SKIP_FORWARD_SECONDS);
          break;
        case "ArrowUp":
          event.preventDefault();
          setVolume(state.volume + 0.1);
          break;
        case "ArrowDown":
          event.preventDefault();
          setVolume(state.volume - 0.1);
          break;
        case "m":
          toggleMute();
          break;
        case "]":
          cycleRate(1);
          break;
        case "[":
          cycleRate(-1);
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasTrack, state.volume]);

  const track = state.track;
  const duration = state.duration || track?.durationSeconds || 0;
  const position = scrubbing ?? state.currentTime;
  const chapter = chapterAt(state.chapters, position);

  return (
    <>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- podcast audio; transcripts are served as feed assets */}
      <audio
        ref={audioRef}
        preload="none"
        onTimeUpdate={(event) => reportPlayback({ currentTime: event.currentTarget.currentTime })}
        onDurationChange={(event) => reportPlayback({ duration: event.currentTarget.duration })}
        onEnded={() => reportPlayback({ playing: false })}
        onPause={() => reportPlayback({ playing: false })}
        onPlay={() => reportPlayback({ playing: true, error: null })}
        onError={() => reportPlayback({ playing: false, error: "The media could not be played" })}
      />
      {track ? (
        <div
          className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80"
          role="region"
          aria-label="Player"
        >
          <div className="mx-auto flex max-w-6xl flex-col gap-1.5 px-4 py-2">
            <div className="flex items-center gap-3">
              <Artwork src={track.artworkUrl} size={40} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{track.title}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {chapter?.title ? (
                    <span data-testid="player-chapter">{chapter.title} · </span>
                  ) : null}
                  {track.feedTitle}
                  {state.error ? <span className="text-destructive"> · {state.error}</span> : null}
                </div>
              </div>
              <div className="flex items-center gap-0.5">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Back ${SKIP_BACK_SECONDS} seconds`}
                      onClick={() => skip(-SKIP_BACK_SECONDS)}
                      className="relative"
                    >
                      <RotateCcw />
                      <span className="absolute inset-0 flex items-center justify-center text-[9px] font-semibold">
                        {SKIP_BACK_SECONDS}
                      </span>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Back {SKIP_BACK_SECONDS} s (←)</TooltipContent>
                </Tooltip>
                <Button
                  variant="default"
                  size="icon"
                  aria-label={state.playing ? "Pause" : "Play"}
                  onClick={() => (state.playing ? pause() : resume())}
                >
                  {state.playing ? <Pause /> : <Play />}
                </Button>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Forward ${SKIP_FORWARD_SECONDS} seconds`}
                      onClick={() => skip(SKIP_FORWARD_SECONDS)}
                      className="relative"
                    >
                      <RotateCw />
                      <span className="absolute inset-0 flex items-center justify-center text-[9px] font-semibold">
                        {SKIP_FORWARD_SECONDS}
                      </span>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Forward {SKIP_FORWARD_SECONDS} s (→)</TooltipContent>
                </Tooltip>
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="Playback speed"
                    className="w-16 tabular-nums"
                  >
                    <Gauge /> {state.rate}×
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {RATES.map((rate) => (
                    <DropdownMenuItem
                      key={rate}
                      onSelect={() => setRate(rate)}
                      className={cn(rate === state.rate && "font-semibold")}
                    >
                      {rate}×
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
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
                <input
                  type="range"
                  aria-label="Volume"
                  min={0}
                  max={1}
                  step={0.05}
                  value={state.muted ? 0 : state.volume}
                  onChange={(event) => setVolume(Number(event.target.value))}
                  className="h-1.5 w-20 cursor-pointer accent-primary"
                />
              </div>
              <Button variant="ghost" size="icon" aria-label="Stop" onClick={stop}>
                <Square />
              </Button>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
              <span className="w-12 text-right">{formatDuration(position)}</span>
              <div className="relative flex-1">
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
                  className="h-1.5 w-full cursor-pointer accent-primary"
                />
                {duration > 0
                  ? state.chapters.map((mark) => (
                      <span
                        key={mark.startTime}
                        title={mark.title}
                        data-testid="chapter-mark"
                        className="pointer-events-none absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 bg-foreground/50"
                        style={{
                          left: `${(Math.min(mark.startTime, duration) / duration) * 100}%`,
                        }}
                      />
                    ))
                  : null}
              </div>
              <span className="w-12">{formatDuration(duration)}</span>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
