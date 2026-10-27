import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, ListVideo, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { $api, ApiProblem, describeProblem } from "@/api/client";
import { OPS } from "@/api/ops";
import type { YouTubePlaylist } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { count } from "@/lib/labels";

const WATCH_LATER: YouTubePlaylist = {
  id: "WL",
  title: "Watch Later",
  url: "https://www.youtube.com/playlist?list=WL",
  item_count: null,
  captured_feed_id: null,
};

/**
 * Capture from the YouTube app: a dropdown of the account's playlists (through the engine,
 * with the cookies, listed when it opens), each captured with a click as a synced, Automatic
 * feed. The menu stays open so several can be captured in a row.
 */
export function CapturePlaylistMenu() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const playlists = $api.useQuery("get", "/api/youtube/playlists", undefined, {
    enabled: open,
    retry: false,
    staleTime: 60_000,
  });
  const [busy, setBusy] = useState<Set<string>>(new Set());
  // Feeds captured from this menu, shown as captured before the listing is fetched again.
  const [captured, setCaptured] = useState<Map<string, string>>(new Map());
  const create = $api.useMutation("post", "/api/mirrors", { meta: { silent: true } });

  const capture = async (playlist: YouTubePlaylist) => {
    setBusy((current) => new Set(current).add(playlist.id));
    try {
      const mirror = await create.mutateAsync({
        body: { source_url: playlist.url, sync_deletions: true, playlist_capture: true },
      });
      setCaptured((current) => new Map(current).set(playlist.id, mirror.id));
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
    }
  };

  const list = playlists.data?.playlists ?? [];
  // Watch Later first, offered even when the account's listing leaves it out.
  const ordered = [
    list.find((p) => p.id === WATCH_LATER.id) ?? WATCH_LATER,
    ...list.filter((p) => p.id !== WATCH_LATER.id),
  ];
  const problem = playlists.isError ? describeProblem(playlists.error) : null;

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">
          <ListVideo /> Capture a YouTube playlist <ChevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="max-h-(--radix-dropdown-menu-content-available-height) w-80 overflow-y-auto"
      >
        <DropdownMenuLabel className="space-y-1">
          <span className="block">Your YouTube playlists</span>
          <span className="block text-xs font-normal text-muted-foreground">
            A captured playlist stays in sync with YouTube and its videos download when your podcast
            app asks for them.
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {playlists.isPending ? (
          <DropdownMenuItem disabled>
            <Loader2 className="animate-spin" aria-hidden /> Listing your playlists…
          </DropdownMenuItem>
        ) : problem ? (
          <>
            <p className="px-2 py-1.5 text-sm text-muted-foreground" data-testid="capture-error">
              Your playlists could not be listed ({problem.description ?? problem.title}). Store
              your YouTube cookies on the Settings page, then try again.
            </p>
            <DropdownMenuItem asChild>
              <Link to="/settings">Open Settings</Link>
            </DropdownMenuItem>
          </>
        ) : (
          ordered.map((playlist) => {
            const feedId = playlist.captured_feed_id ?? captured.get(playlist.id);
            const details = (
              <>
                <span className="min-w-0 flex-1 truncate">{playlist.title}</span>
                {playlist.item_count != null ? (
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {count(playlist.item_count, "video")}
                  </span>
                ) : null}
              </>
            );
            if (feedId) {
              return (
                <DropdownMenuItem key={playlist.id} asChild>
                  <Link to="/mirrors/$mirrorId" params={{ mirrorId: feedId }}>
                    <Check className="text-success" aria-label="Captured" />
                    {details}
                  </Link>
                </DropdownMenuItem>
              );
            }
            return (
              <DropdownMenuItem
                key={playlist.id}
                disabled={busy.has(playlist.id)}
                onSelect={(event) => {
                  event.preventDefault();
                  void capture(playlist);
                }}
              >
                {busy.has(playlist.id) ? (
                  <Loader2 className="animate-spin" aria-label="Capturing" />
                ) : (
                  <ListVideo aria-hidden />
                )}
                {details}
              </DropdownMenuItem>
            );
          })
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
