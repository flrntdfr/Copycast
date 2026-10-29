/** Mutations of the bucket page: purging a Feed's bucket. */
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { formatMegabytes } from "@/lib/format";
import { count } from "@/lib/labels";

/** `clear_feed`: deletes every Episode that is not locked; the dry run counts them first. */
export function useClearFeed() {
  const queryClient = useQueryClient();
  const invalidateFeed = useInvalidateFeed();
  return $api.useMutation("post", "/api/feeds/{feed_id}/clear", {
    onSuccess: (result, { params, body }) => {
      if (body.dry_run) return;
      const feedId = params.path.feed_id;
      invalidateFeed(feedId);
      void queryClient.invalidateQueries({
        queryKey: [OPS.storage_summary[0], OPS.storage_summary[1]],
      });
      void queryClient.invalidateQueries({
        queryKey: [OPS.list_items[0], OPS.list_items[1], { params: { path: { feed_id: feedId } } }],
      });
      const kept = result.kept_count ?? 0;
      toast.success(`${count(result.deleted_count, "Episode")} deleted`, {
        description: `${formatMegabytes(result.bytes_freed)} freed${kept ? `; ${count(kept, "locked Episode")} kept` : ""}.`,
      });
    },
  });
}
