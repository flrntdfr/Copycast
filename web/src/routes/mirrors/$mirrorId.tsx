import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { z } from "zod";

import { $api, ApiProblem, asError } from "@/api/client";
import { isMirror } from "@/api/types";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { CatalogTable } from "@/features/catalog/CatalogTable";
import { catalogSearchDefaults, catalogSearchSchema } from "@/features/catalog/search";
import { useRunningRefreshFeedIds } from "@/features/jobs/useRunningJobs";
import { MirrorHeader } from "@/features/mirrors/MirrorHeader";

const defaults = { settings: false, ...catalogSearchDefaults } as const;

export const Route = createFileRoute("/mirrors/$mirrorId")({
  validateSearch: catalogSearchSchema.extend({
    /** `?settings=true` opens the Settings dialog once (a Feeds-page menu links here). */
    settings: z.boolean().default(defaults.settings),
  }),
  search: { middlewares: [stripSearchParams(defaults)] },
  component: MirrorPage,
});

function MirrorPage() {
  const { mirrorId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const feed = $api.useQuery("get", "/api/feeds/{feed_id}", {
    params: { path: { feed_id: mirrorId } },
  });
  const refreshing = useRunningRefreshFeedIds();

  // Remember the request before the URL is cleaned, so a reload does not reopen the dialog.
  const [settingsRequested] = useState(search.settings);
  useEffect(() => {
    if (!search.settings) return;
    void navigate({ search: (prev) => ({ ...prev, settings: false }), replace: true });
  }, [search.settings, navigate]);

  if (feed.isPending) return <PageSpinner />;
  if (feed.isError) {
    if (ApiProblem.is(feed.error) && feed.error.status === 404) return <NotFound />;
    throw asError(feed.error);
  }
  if (!isMirror(feed.data)) return <NotFound />;
  const mirror = feed.data;

  return (
    <>
      <MirrorHeader
        mirror={mirror}
        refreshing={refreshing.has(mirror.id)}
        initialSettingsOpen={settingsRequested}
      />
      <CatalogTable
        feed={mirror}
        search={search}
        onSearchChange={(patch) =>
          void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true })
        }
      />
    </>
  );
}
