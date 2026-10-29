import { createFileRoute } from "@tanstack/react-router";

import { $api, ApiProblem, asError } from "@/api/client";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";
import { BucketHeader } from "@/features/buckets/BucketHeader";
import { FeedObjectsTable } from "@/features/buckets/FeedObjectsTable";

/**
 * The bucket of any Feed (Mirror or Local feed): the files it keeps under `feeds/{id}/` in the
 * Object store, each downloadable and lockable, and the Purge of everything not locked.
 */
export const Route = createFileRoute("/buckets/$bucketId")({
  component: BucketPage,
});

function BucketPage() {
  const { bucketId } = Route.useParams();
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
      <BucketHeader feed={feed.data} />
      <FeedObjectsTable feed={feed.data} />
    </>
  );
}
