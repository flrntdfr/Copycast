import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { z } from "zod";

import { $api, ApiProblem, asError } from "@/api/client";
import { isMirror } from "@/api/types";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CatalogTable } from "@/features/catalog/CatalogTable";
import { catalogSearchDefaults, catalogSearchSchema } from "@/features/catalog/search";
import { useRunningRefreshFeedIds } from "@/features/jobs/useRunningJobs";
import { MirrorHeader } from "@/features/mirrors/MirrorHeader";
import { MirrorSettingsForm } from "@/features/mirrors/MirrorSettingsForm";
import { RefreshesTable } from "@/features/mirrors/RefreshesTable";

const MIRROR_TABS = ["catalog", "refreshes", "settings"] as const;
type MirrorTab = (typeof MIRROR_TABS)[number];

const defaults = { tab: "catalog", ...catalogSearchDefaults } as const;

export const Route = createFileRoute("/mirrors/$mirrorId")({
  validateSearch: catalogSearchSchema.extend({ tab: z.enum(MIRROR_TABS).default(defaults.tab) }),
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

  if (feed.isPending) return <PageSpinner />;
  if (feed.isError) {
    if (ApiProblem.is(feed.error) && feed.error.status === 404) return <NotFound />;
    throw asError(feed.error);
  }
  if (!isMirror(feed.data)) return <NotFound />;
  const mirror = feed.data;

  return (
    <>
      <MirrorHeader mirror={mirror} refreshing={refreshing.has(mirror.id)} />
      <Tabs
        value={search.tab}
        onValueChange={(tab) =>
          void navigate({ search: (prev) => ({ ...prev, tab: tab as MirrorTab }) })
        }
      >
        <TabsList aria-label="Mirror sections">
          <TabsTrigger value="catalog">Catalog</TabsTrigger>
          <TabsTrigger value="refreshes">Refreshes</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="catalog" className="mt-4">
          <CatalogTable
            feed={mirror}
            search={search}
            onSearchChange={(patch) =>
              void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true })
            }
          />
        </TabsContent>
        <TabsContent value="refreshes" className="mt-4">
          <RefreshesTable feedId={mirror.id} />
        </TabsContent>
        <TabsContent value="settings" className="mt-4">
          <MirrorSettingsForm mirror={mirror} />
        </TabsContent>
      </Tabs>
    </>
  );
}
