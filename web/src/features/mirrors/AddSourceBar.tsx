import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";

import { RotatingPlaceholder } from "@/components/common/RotatingPlaceholder";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { looksLikeSourceUrl, openAddSource } from "@/stores/addSource";

/** What the bar accepts, one hint at a time while the field is empty. */
export const ADD_SOURCE_HINTS = [
  "Paste a podcast RSS feed URL…",
  "Paste a YouTube channel URL…",
  "Paste a YouTube playlist URL…",
  "Paste an Apple Podcasts or Spotify link…",
  "Type a podcast name to search…",
] as const;

/** The prominent "paste a URL" bar above the Feeds table; opens the Add Source dialog. */
export function AddSourceBar({ autoFocus = false }: { autoFocus?: boolean }) {
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
      className="flex flex-col gap-2 rounded-lg border bg-card p-3 sm:flex-row sm:items-end"
    >
      <div className="relative flex-1">
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
          A podcast RSS feed, a YouTube channel or playlist, an Apple Podcasts or Spotify link, or a
          podcast name to search for.
        </span>
      </div>
      <Button type="submit" disabled={!url.trim()}>
        Mirror <ArrowRight />
      </Button>
    </form>
  );
}
