import { Pause, Play, Square } from "lucide-react";
import { useEffect, useRef } from "react";

import { Artwork } from "@/components/common/Artwork";
import { Button } from "@/components/ui/button";
import { formatDuration } from "@/lib/format";
import { pause, reportPlayback, resume, stop, usePlayer } from "@/stores/player";

/** The one `<audio preload="none">` element in the app, driven by the player store. */
export function PlayerBar() {
  const state = usePlayer();
  const audioRef = useRef<HTMLAudioElement | null>(null);

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

  const track = state.track;
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
          <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-2">
            <Artwork src={track.artworkUrl} size={36} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{track.title}</div>
              <div className="truncate text-xs text-muted-foreground">
                {track.feedTitle}
                {state.error ? <span className="text-destructive"> · {state.error}</span> : null}
              </div>
            </div>
            <div className="hidden text-xs text-muted-foreground tabular-nums sm:block">
              {formatDuration(state.currentTime)} /{" "}
              {formatDuration(state.duration || track.durationSeconds)}
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label={state.playing ? "Pause" : "Play"}
              onClick={() => (state.playing ? pause() : resume())}
            >
              {state.playing ? <Pause /> : <Play />}
            </Button>
            <Button variant="ghost" size="icon" aria-label="Stop" onClick={stop}>
              <Square />
            </Button>
          </div>
        </div>
      ) : null}
    </>
  );
}
