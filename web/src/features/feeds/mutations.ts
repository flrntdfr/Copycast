/** Mutations shared by the Mirrors list, the Mirror header and the Settings tab. */
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";
import type { FeedRead, MirrorRead } from "@/api/types";

export function useInvalidateFeed() {
  const queryClient = useQueryClient();
  return (feedId?: string) => {
    void queryClient.invalidateQueries({ queryKey: [OPS.list_feeds[0], OPS.list_feeds[1]] });
    if (feedId) {
      void queryClient.invalidateQueries({
        queryKey: [OPS.get_feed[0], OPS.get_feed[1], { params: { path: { feed_id: feedId } } }],
      });
      void queryClient.invalidateQueries({ queryKey: [OPS.list_jobs[0], OPS.list_jobs[1]] });
    }
  };
}

export function useRequestRefresh() {
  const invalidate = useInvalidateFeed();
  return $api.useMutation("post", "/api/mirrors/{feed_id}/refresh", {
    onSuccess: (job, variables) => {
      invalidate(variables.params.path.feed_id);
      toast.success(job.status === "running" ? "A Refresh is already running" : "Refresh queued");
    },
  });
}

export function useSetPaused() {
  const invalidate = useInvalidateFeed();
  const pause = $api.useMutation("post", "/api/mirrors/{feed_id}/pause", {
    onSuccess: (mirror: MirrorRead) => {
      invalidate(mirror.id);
      toast.success(`${mirror.title} paused`, { description: "It keeps serving its Mirror Feed." });
    },
  });
  const resume = $api.useMutation("post", "/api/mirrors/{feed_id}/resume", {
    onSuccess: (mirror: MirrorRead) => {
      invalidate(mirror.id);
      toast.success(`${mirror.title} resumed`, { description: "A Refresh was queued." });
    },
  });
  return {
    setPaused: (feedId: string, paused: boolean) =>
      paused
        ? pause.mutateAsync({ params: { path: { feed_id: feedId } } })
        : resume.mutateAsync({ params: { path: { feed_id: feedId } } }),
    isPending: pause.isPending || resume.isPending,
  };
}

export function useDeleteFeed() {
  const invalidate = useInvalidateFeed();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const mutation = $api.useMutation("delete", "/api/feeds/{feed_id}");
  return {
    ...mutation,
    deleteFeed: async (
      feed: Pick<FeedRead, "id" | "title" | "kind">,
      { navigateAway = true } = {},
    ) => {
      await mutation.mutateAsync({ params: { path: { feed_id: feed.id } } });
      queryClient.removeQueries({
        queryKey: [OPS.get_feed[0], OPS.get_feed[1], { params: { path: { feed_id: feed.id } } }],
      });
      invalidate();
      toast.success(`${feed.title} deleted`);
      if (navigateAway) {
        await navigate({ to: feed.kind === "mirror" ? "/mirrors" : "/inboxes", replace: true });
      }
    },
  };
}
