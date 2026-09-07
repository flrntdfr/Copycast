import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";

import { $api, describeProblem } from "@/api/client";
import { Artwork } from "@/components/common/Artwork";
import { EmptyState } from "@/components/common/EmptyState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useSendToInbox } from "@/features/inboxes/useSendToInbox";
import { formatDate, formatDuration } from "@/lib/format";
import { count } from "@/lib/labels";

/** iTunes search (`search_podcasts`) prefilling the wizard, plus YouTube hits (`search_videos`) to send to an Inbox. */
export function SearchStep({
  query,
  onPick,
}: {
  query: string;
  onPick: (feedUrl: string) => void;
}) {
  const [draft, setDraft] = useState(query);
  const [term, setTerm] = useState(query);
  const search = $api.useQuery(
    "get",
    "/api/search/podcasts",
    { params: { query: { query: term, limit: 10 } } },
    { enabled: term.trim().length > 0 },
  );
  const videos = $api.useQuery(
    "get",
    "/api/search/videos",
    { params: { query: { query: term, limit: 8 } } },
    { enabled: term.trim().length > 0 },
  );
  const inbox = useSendToInbox();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTerm(draft.trim());
  };

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="flex max-w-2xl gap-2">
        <Label htmlFor="podcast-search" className="sr-only">
          Podcast or video name
        </Label>
        <Input
          id="podcast-search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Podcast or video name"
          autoFocus
        />
        <Button type="submit" disabled={!draft.trim()}>
          <Search /> Search
        </Button>
      </form>
      {search.isPending && term ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : null}
      {search.isError ? (
        <p className="text-sm text-destructive">{describeProblem(search.error).title}</p>
      ) : null}
      {search.data?.results.length === 0 ? (
        <EmptyState
          title="No podcasts found"
          description={`Nothing in the iTunes directory matches “${term}”. Paste the feed URL directly instead.`}
        />
      ) : null}
      <ul className="divide-y rounded-lg border" aria-label="Search results">
        {(search.data?.results ?? []).map((result) => (
          <li key={result.feed_url} className="flex items-center gap-3 p-3">
            <Artwork src={result.artwork_url} size={48} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{result.title}</div>
              <div className="truncate text-xs text-muted-foreground">
                {[
                  result.author,
                  result.genre,
                  result.episode_count != null ? count(result.episode_count, "episode") : null,
                  result.latest_release_at
                    ? `latest ${formatDate(result.latest_release_at)}`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
            <Button size="sm" onClick={() => onPick(result.feed_url)}>
              Mirror
            </Button>
          </li>
        ))}
      </ul>
      {term ? (
        <section aria-labelledby="video-results" className="space-y-2">
          <h2 id="video-results" className="text-sm font-medium">
            Videos on YouTube
          </h2>
          {videos.isPending ? <Skeleton className="h-16 w-full" /> : null}
          {videos.isError ? (
            <p className="text-sm text-destructive">{describeProblem(videos.error).title}</p>
          ) : null}
          {videos.data?.results.length === 0 ? (
            <p className="text-sm text-muted-foreground">No videos match “{term}”.</p>
          ) : null}
          <ul className="divide-y rounded-lg border" aria-label="Video results">
            {(videos.data?.results ?? []).map((video) => (
              <li key={video.url} className="flex items-center gap-3 p-3">
                <Artwork src={video.artwork_url} size={48} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{video.title}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {[
                      video.channel,
                      video.duration_seconds != null
                        ? formatDuration(video.duration_seconds)
                        : null,
                      video.published_at ? formatDate(video.published_at) : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={inbox.isPending}
                  onClick={() => void inbox.send(video.url)}
                >
                  Send to Inbox
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
