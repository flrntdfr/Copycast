import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { looksLikeSourceUrl, openAddSource } from "@/stores/addSource";

/** The prominent "paste a URL" bar above the Mirrors table; opens the Add Source dialog. */
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
      <div className="flex-1">
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
          placeholder="Paste a podcast feed, an Apple Podcasts or Spotify link, a YouTube channel or playlist, or a name to search…"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
      </div>
      <Button type="submit" disabled={!url.trim()}>
        Add Source <ArrowRight />
      </Button>
    </form>
  );
}
