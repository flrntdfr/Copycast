import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { isBucket, type BucketRead } from "@/api/types";

/** The default Local feed when listed, else the one named "Copycast", else the first. */
export function pickDefaultBucket(buckets: readonly BucketRead[]): BucketRead | null {
  return (
    buckets.find((bucket) => bucket.is_default) ??
    buckets.find((bucket) => bucket.name.toLowerCase() === "copycast") ??
    buckets[0] ??
    null
  );
}

/** Push a URL into a Local feed as a Request and open that Local feed's page. */
export function useSendToBucket() {
  const navigate = useNavigate();
  const buckets = $api.useQuery("get", "/api/feeds", { params: { query: { kind: "bucket" } } });
  const candidates = (buckets.data?.feeds ?? []).filter(isBucket);
  const mutation = $api.useMutation("post", "/api/buckets/{bucket_id}/requests", {
    onSuccess: (request) => {
      const name = candidates.find((bucket) => bucket.id === request.bucket_id)?.name;
      toast.success(name ? `Sent to ${name}` : "Sent to the Local feed", {
        description: request.url,
      });
      void navigate({ to: "/feed/$feedId", params: { feedId: request.bucket_id } });
    },
  });
  const target = pickDefaultBucket(candidates);
  return {
    buckets: candidates,
    target,
    isPending: mutation.isPending,
    send: (url: string, bucketId = target?.id) => {
      if (!bucketId) {
        toast.error("No Local feed is available yet");
        return Promise.resolve(null);
      }
      return mutation.mutateAsync({ params: { path: { bucket_id: bucketId } }, body: { url } });
    },
  };
}
