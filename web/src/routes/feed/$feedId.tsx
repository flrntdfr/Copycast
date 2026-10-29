import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { $api, ApiProblem, asError } from "@/api/client";
import { isMirror } from "@/api/types";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { CatalogTable } from "@/features/catalog/CatalogTable";
import { LocalFeedHeader } from "@/features/feeds/LocalFeedHeader";
import { feedSearchDefaults, feedSearchSchema } from "@/features/feeds/search";
import { useRunningRefreshFeedIds } from "@/features/jobs/useRunningJobs";
import { MirrorHeader } from "@/features/mirrors/MirrorHeader";

/** The page of any Feed, a Mirror (captured playlists too) or a Local feed: header and Catalog. */
export const Route = createFileRoute("/feed/$feedId")({
  validateSearch: feedSearchSchema,
  search: { middlewares: [stripSearchParams(feedSearchDefaults)] },
  component: FeedPage,
});

function FeedPage() {
  const { feedId } = Route.useParams();
  // A new feed is a new page: its one-shot requests are read again.
  return <FeedView key={feedId} feedId={feedId} />;
}

function FeedView({ feedId }: { feedId: string }) {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const feed = $api.useQuery("get", "/api/feeds/{feed_id}", {
    params: { path: { feed_id: feedId } },
  });
  const refreshing = useRunningRefreshFeedIds();

  // Remember the one-shot requests before the URL is cleaned, so a reload does not repeat them.
  const [settingsRequested] = useState(search.settings);
  const [initialUrl] = useState(search.url);
  useEffect(() => {
    if (!search.settings && !search.url) return;
    void navigate({ search: (prev) => ({ ...prev, settings: false, url: "" }), replace: true });
  }, [search.settings, search.url, navigate]);

  if (feed.isPending) return <PageSpinner />;
  if (feed.isError) {
    if (ApiProblem.is(feed.error) && feed.error.status === 404) return <NotFound />;
    throw asError(feed.error);
  }

  return (
    <>
      {isMirror(feed.data) ? (
        <MirrorHeader
          mirror={feed.data}
          refreshing={refreshing.has(feed.data.id)}
          initialSettingsOpen={settingsRequested}
        />
      ) : (
        <LocalFeedHeader feed={feed.data} initialUrl={initialUrl} />
      )}
      <CatalogTable
        feed={feed.data}
        search={search}
        onSearchChange={(patch) =>
          void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true })
        }
      />
    </>
  );
}
