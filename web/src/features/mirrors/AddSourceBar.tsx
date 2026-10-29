import { ArrowRight } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { RotatingPlaceholder } from "@/components/common/RotatingPlaceholder";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { looksLikeSourceUrl, openAddSource } from "@/stores/addSource";

/**
 * What the bar accepts, one hint at a time while the field is empty: a podcast feed, the sites
 * the Engine (yt-dlp) knows best, and a name to search for.
 */
export const ADD_SOURCE_HINTS = [
  "Paste a podcast RSS feed URL…",
  "Paste a YouTube channel URL…",
  "Paste a YouTube playlist URL…",
  "Paste an Apple Podcasts link…",
  "Paste a Spotify show link…",
  "Paste a SoundCloud artist or playlist URL…",
  "Paste a Vimeo channel or showcase URL…",
  "Paste a Twitch channel URL…",
  "Paste a Bandcamp album URL…",
  "Paste a Mixcloud show URL…",
  "Paste a Dailymotion channel URL…",
  "Paste an Internet Archive collection URL…",
  "Type a podcast name to search…",
] as const;

/**
 * The prominent "paste a URL" bar at the top of the Feeds page; opens the Add Source dialog.
 * `children` are more actions after the Mirror button (the Feeds page's Playlists menu); they
 * sit inside the form, so they must be `type="button"`.
 */
export function AddSourceBar({
  autoFocus = false,
  children,
}: {
  autoFocus?: boolean;
  children?: ReactNode;
}) {
  const [url, setUrl] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    openAddSource(looksLikeSourceUrl(trimmed) ? { url: trimmed } : { query: trimmed });
    setUrl("");
  };

  return (
    <form
      onSubmit={submit}
      className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3 sm:flex-nowrap"
    >
      <div className="relative min-w-0 basis-full sm:flex-1 sm:basis-auto">
        <Label htmlFor="add-source-url" className="sr-only">
          Source URL
        </Label>
        <Input
          id="add-source-url"
          name="url"
          type="text"
          inputMode="url"
          autoComplete="off"
          autoFocus={autoFocus}
          placeholder=""
          aria-describedby="add-source-hint"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        {/* The browser cannot animate `placeholder`, so the hint is drawn over the empty field. */}
        <RotatingPlaceholder hints={ADD_SOURCE_HINTS} active={url === ""} />
        <span id="add-source-hint" className="sr-only">
          A podcast feed, a YouTube channel or playlist, an Apple Podcasts or Spotify link, a page
          from SoundCloud, Vimeo, Twitch, Bandcamp and other sites, or a podcast name to search for.
        </span>
      </div>
      <Button type="submit" disabled={!url.trim()}>
        Mirror <ArrowRight />
      </Button>
      {children}
    </form>
  );
}
