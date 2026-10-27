import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { isBucket, type BucketRead } from "@/api/types";

/** The Bucket named "Copycast" when it exists (the default one unless renamed), else the first. */
export function pickDefaultBucket(buckets: readonly BucketRead[]): BucketRead | null {
  return buckets.find((bucket) => bucket.name.toLowerCase() === "copycast") ?? buckets[0] ?? null;
}

/** Push a URL into a Bucket as a Request and open that Bucket. */
export function useSendToBucket() {
  const navigate = useNavigate();
  const buckets = $api.useQuery("get", "/api/feeds", { params: { query: { kind: "bucket" } } });
  const mutation = $api.useMutation("post", "/api/buckets/{bucket_id}/requests", {
    onSuccess: (request) => {
      toast.success("Sent to the Bucket", { description: request.url });
      void navigate({
        to: "/buckets/$bucketId",
        params: { bucketId: request.bucket_id },
        search: { tab: "requests" },
      });
    },
  });
  const candidates = (buckets.data?.feeds ?? []).filter(isBucket);
  const target = pickDefaultBucket(candidates);
  return {
    buckets: candidates,
    target,
    isPending: mutation.isPending,
    send: (url: string, bucketId = target?.id) => {
      if (!bucketId) {
        toast.error("No Bucket is available yet");
        return Promise.resolve(null);
      }
      return mutation.mutateAsync({ params: { path: { bucket_id: bucketId } }, body: { url } });
    },
  };
}
