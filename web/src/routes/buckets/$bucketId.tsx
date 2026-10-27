import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { z } from "zod";

import { $api, ApiProblem, asError } from "@/api/client";
import { isBucket } from "@/api/types";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CatalogTable } from "@/features/catalog/CatalogTable";
import { catalogSearchDefaults, catalogSearchSchema } from "@/features/catalog/search";
import { BucketHeader } from "@/features/buckets/BucketHeader";
import { BucketSettings } from "@/features/buckets/BucketSettings";
import { RequestsTable } from "@/features/buckets/RequestsTable";

const BUCKET_TABS = ["episodes", "requests", "settings"] as const;
type BucketTab = (typeof BUCKET_TABS)[number];

const defaults = { tab: "episodes", url: "", ...catalogSearchDefaults } as const;

export const Route = createFileRoute("/buckets/$bucketId")({
  validateSearch: catalogSearchSchema.extend({
    tab: z.enum(BUCKET_TABS).default(defaults.tab),
    url: z.string().default(defaults.url),
  }),
  search: { middlewares: [stripSearchParams(defaults)] },
  component: BucketPage,
});

function BucketPage() {
  const { bucketId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const feed = $api.useQuery("get", "/api/feeds/{feed_id}", {
    params: { path: { feed_id: bucketId } },
  });

  if (feed.isPending) return <PageSpinner />;
  if (feed.isError) {
    if (ApiProblem.is(feed.error) && feed.error.status === 404) return <NotFound />;
    throw asError(feed.error);
  }
  if (!isBucket(feed.data)) return <NotFound />;
  const bucket = feed.data;

  return (
    <>
      <BucketHeader bucket={bucket} initialUrl={search.url} />
      <Tabs
        value={search.tab}
        onValueChange={(tab) =>
          void navigate({ search: (prev) => ({ ...prev, tab: tab as BucketTab }) })
        }
      >
        <TabsList aria-label="Bucket sections">
          <TabsTrigger value="episodes">Episodes</TabsTrigger>
          <TabsTrigger value="requests">Requests</TabsTrigger>
          <TabsTrigger value="settings">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="episodes" className="mt-4">
          <CatalogTable
            feed={bucket}
            search={search}
            onSearchChange={(patch) =>
              void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true })
            }
          />
        </TabsContent>
        <TabsContent value="requests" className="mt-4">
          <RequestsTable bucketId={bucket.id} />
        </TabsContent>
        <TabsContent value="settings" className="mt-4">
          <BucketSettings bucket={bucket} />
        </TabsContent>
      </Tabs>
    </>
  );
}
