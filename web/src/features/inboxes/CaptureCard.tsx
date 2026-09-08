import { ListVideo } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { openAddSource } from "@/stores/addSource";

/**
 * Capture from the YouTube app: a private playlist mirrored in sync, so sharing a video
 * to the playlist from the app puts it in a Copycast feed.
 */
export function CaptureCard() {
  const [url, setUrl] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    openAddSource({ url: trimmed, sync: true });
    setUrl("");
  };
  return (
    <Card data-testid="capture-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ListVideo className="size-4" aria-hidden /> Capture from the YouTube app
        </CardTitle>
        <CardDescription>
          Make a private playlist on YouTube (for instance “Copycast”), share videos to it from the
          YouTube app, and paste its link here. Copycast mirrors the playlist as its own feed and
          keeps it in sync: a video added to the playlist appears in the feed, a video removed from
          it goes. Episodes download when your podcast app asks for them. Your cookies on the
          Settings page let Copycast see a private playlist.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1">
            <Label htmlFor="capture-playlist-url" className="sr-only">
              Playlist link
            </Label>
            <Input
              id="capture-playlist-url"
              inputMode="url"
              autoComplete="off"
              placeholder="https://www.youtube.com/playlist?list=PL…"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          </div>
          <Button type="submit" disabled={!url.trim()}>
            <ListVideo /> Mirror the playlist
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
