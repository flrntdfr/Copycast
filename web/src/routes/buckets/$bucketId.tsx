import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { z } from "zod";

import { $api, ApiProblem, asError } from "@/api/client";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { BucketHeader } from "@/features/buckets/BucketHeader";
import { FeedObjectsTable } from "@/features/buckets/FeedObjectsTable";

const defaults = { url: "" } as const;

/**
 * The storage view of any Feed (Mirror or Bucket): what it keeps under `feeds/{id}/` in the
 * Object store. `?url=` prefills a Bucket Feed's Add URL form (the Send-to-Bucket flow).
 */
export const Route = createFileRoute("/buckets/$bucketId")({
  validateSearch: z.object({ url: z.string().default(defaults.url) }),
  search: { middlewares: [stripSearchParams(defaults)] },
  component: BucketPage,
});

function BucketPage() {
  const { bucketId } = Route.useParams();
  const search = Route.useSearch();
  const feed = $api.useQuery("get", "/api/feeds/{feed_id}", {
    params: { path: { feed_id: bucketId } },
  });

  if (feed.isPending) return <PageSpinner />;
  if (feed.isError) {
    if (ApiProblem.is(feed.error) && feed.error.status === 404) return <NotFound />;
    throw asError(feed.error);
  }

  return (
    <>
      <BucketHeader feed={feed.data} initialUrl={search.url} />
      <FeedObjectsTable feed={feed.data} />
    </>
  );
}
