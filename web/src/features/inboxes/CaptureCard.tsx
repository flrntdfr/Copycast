import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ListVideo, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { $api, ApiProblem, describeProblem } from "@/api/client";
import { OPS } from "@/api/ops";
import type { YouTubePlaylist } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { count } from "@/lib/labels";

const WATCH_LATER_URL = "https://www.youtube.com/playlist?list=WL";

/**
 * Capture from the YouTube app: the account's playlists (through the engine, with the
 * cookies), captured one click at a time as synced, Automatic feeds on this screen.
 */
export function CaptureCard() {
  const queryClient = useQueryClient();
  const playlists = $api.useQuery("get", "/api/youtube/playlists", undefined, {
    retry: false,
    staleTime: 60_000,
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const create = $api.useMutation("post", "/api/mirrors", { meta: { silent: true } });

  const capture = async (playlist: Pick<YouTubePlaylist, "id" | "title" | "url">) => {
    setBusy((current) => new Set(current).add(playlist.id));
    try {
      const mirror = await create.mutateAsync({
        body: { source_url: playlist.url, sync_deletions: true, playlist_capture: true },
      });
      toast.success(`“${mirror.title}” captured`, {
        description: "Its videos download when your podcast app asks for them.",
      });
      void queryClient.invalidateQueries({ queryKey: [OPS.list_feeds[0], OPS.list_feeds[1]] });
      void queryClient.invalidateQueries({
        queryKey: [OPS.list_youtube_playlists[0], OPS.list_youtube_playlists[1]],
      });
    } catch (error) {
      if (ApiProblem.is(error) && error.slug === "feed-exists") {
        toast.info(`“${playlist.title}” is already captured`);
        return;
      }
      const { title, description } = describeProblem(error);
      toast.error(`Could not capture “${playlist.title}”`, { description: description ?? title });
    } finally {
      setBusy((current) => {
        const next = new Set(current);
        next.delete(playlist.id);
        return next;
      });
      setSelected((current) => {
        const next = new Set(current);
        next.delete(playlist.id);
        return next;
      });
    }
  };

  const list = playlists.data?.playlists ?? [];
  const watchLater = list.find((p) => p.id === "WL") ?? {
    id: "WL",
    title: "Watch Later",
    url: WATCH_LATER_URL,
    item_count: null,
    captured_feed_id: null,
  };
  const others = list.filter((p) => p.id !== "WL");
  const importable = others.filter((p) => !p.captured_feed_id);
  const chosen = importable.filter((p) => selected.has(p.id));
  const problem = playlists.isError ? describeProblem(playlists.error) : null;

  return (
    <Card data-testid="capture-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ListVideo className="size-4" aria-hidden /> Capture from the YouTube app
        </CardTitle>
        <CardDescription>
          Share a video to a playlist from the YouTube app and it lands in a Copycast feed: each
          captured playlist is kept in sync (add a video, it appears; remove it, it goes) and its
          videos download when your podcast app asks for them. Captured playlists live here, next to
          your Inboxes. Copycast reads your playlists with the cookies stored on the{" "}
          <Link to="/settings" className="underline">
            Settings
          </Link>{" "}
          page.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          {watchLater.captured_feed_id ? (
            <Button variant="outline" asChild>
              <Link to="/mirrors/$mirrorId" params={{ mirrorId: watchLater.captured_feed_id }}>
                <Check /> Watch Later is captured
              </Link>
            </Button>
          ) : (
            <Button onClick={() => void capture(watchLater)} disabled={busy.has("WL")}>
              {busy.has("WL") ? <Loader2 className="animate-spin" /> : <ListVideo />}
              Keep Watch Later as an Inbox
            </Button>
          )}
          <span className="text-xs text-muted-foreground">
            One click: everything you save to Watch Later becomes an episode.
          </span>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-medium">Your playlists</p>
          {playlists.isPending ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2 className="size-4 animate-spin" aria-hidden /> Listing your playlists…
            </p>
          ) : problem ? (
            <p className="text-sm text-muted-foreground" data-testid="capture-error">
              Your playlists could not be listed ({problem.description ?? problem.title}). Store
              your YouTube cookies on the{" "}
              <Link to="/settings" className="underline">
                Settings
              </Link>{" "}
              page, then try again.
            </p>
          ) : others.length === 0 ? (
            <p className="text-sm text-muted-foreground">No playlists besides Watch Later.</p>
          ) : (
            <ul className="divide-y rounded-lg border" aria-label="Your playlists">
              {others.map((playlist) => (
                <li key={playlist.id} className="flex items-center gap-3 p-2">
                  {playlist.captured_feed_id ? (
                    <Check className="size-4 text-success" aria-label="Captured" />
                  ) : (
                    <Checkbox
                      id={`playlist-${playlist.id}`}
                      checked={selected.has(playlist.id)}
                      disabled={busy.has(playlist.id)}
                      onCheckedChange={(value) =>
                        setSelected((current) => {
                          const next = new Set(current);
                          if (value === true) next.add(playlist.id);
                          else next.delete(playlist.id);
                          return next;
                        })
                      }
                    />
                  )}
                  <Label
                    htmlFor={`playlist-${playlist.id}`}
                    className="flex min-w-0 flex-1 items-center gap-2 font-normal"
                  >
                    <span className="truncate">{playlist.title}</span>
                    {playlist.item_count != null ? (
                      <span className="text-xs text-muted-foreground">
                        {count(playlist.item_count, "video")}
                      </span>
                    ) : null}
                  </Label>
                  {playlist.captured_feed_id ? (
                    <Button variant="ghost" size="sm" asChild>
                      <Link
                        to="/mirrors/$mirrorId"
                        params={{ mirrorId: playlist.captured_feed_id }}
                      >
                        Open
                      </Link>
                    </Button>
                  ) : busy.has(playlist.id) ? (
                    <Loader2 className="size-4 animate-spin" aria-label="Capturing" />
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          {importable.length ? (
            <Button
              variant="outline"
              disabled={chosen.length === 0 || busy.size > 0}
              onClick={() => {
                void (async () => {
                  for (const playlist of chosen) await capture(playlist);
                })();
              }}
            >
              {busy.size > 0 && chosen.length ? <Loader2 className="animate-spin" /> : null}
              Import {chosen.length ? count(chosen.length, "selected playlist") : "selected"}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
