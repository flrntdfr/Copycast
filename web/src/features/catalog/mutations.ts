import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";
import type { ItemRead } from "@/api/types";

export function useInvalidateCatalog(feedId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({
      queryKey: [OPS.list_items[0], OPS.list_items[1], { params: { path: { feed_id: feedId } } }],
    });
    void queryClient.invalidateQueries({
      queryKey: [OPS.get_feed[0], OPS.get_feed[1], { params: { path: { feed_id: feedId } } }],
    });
    void queryClient.invalidateQueries({ queryKey: [OPS.list_jobs[0], OPS.list_jobs[1]] });
  };
}

/** `archive_item` for one row (Add / Retry). */
export function useArchiveItem(feedId: string) {
  const invalidate = useInvalidateCatalog(feedId);
  return $api.useMutation("post", "/api/feeds/{feed_id}/items/{item_id}/archive", {
    onSuccess: () => {
      invalidate();
      toast.success("Queued for archiving");
    },
  });
}

/** `fetch_item_metadata` for one row: the Source's description, exact date and artwork. */
export function useFetchMetadata(feedId: string) {
  const invalidate = useInvalidateCatalog(feedId);
  return $api.useMutation("post", "/api/feeds/{feed_id}/items/{item_id}/metadata", {
    meta: { silent: true },
    onSuccess: () => invalidate(),
  });
}

/** `delete_item` for one row; leaves a tombstone. */
export function useDeleteItem(feedId: string) {
  const invalidate = useInvalidateCatalog(feedId);
  return $api.useMutation("delete", "/api/feeds/{feed_id}/items/{item_id}", {
    onSuccess: () => {
      invalidate();
      toast.success("Episode deleted", {
        description: "It stays Available and will not re-archive on its own.",
      });
    },
  });
}

/** Bulk helpers for the SelectionBar; per-item calls so Inboxes work too. */
export function useBulkActions(feedId: string, feedKind: "mirror" | "inbox") {
  const invalidate = useInvalidateCatalog(feedId);
  const select = $api.useMutation("post", "/api/mirrors/{feed_id}/selections", {
    meta: { silent: true },
  });
  const archive = $api.useMutation("post", "/api/feeds/{feed_id}/items/{item_id}/archive", {
    meta: { silent: true },
  });
  const remove = $api.useMutation("delete", "/api/feeds/{feed_id}/items/{item_id}", {
    meta: { silent: true },
  });

  const archiveMany = async (items: ItemRead[]) => {
    if (items.length === 0) return;
    if (feedKind === "mirror") {
      const result = await select.mutateAsync({
        params: { path: { feed_id: feedId } },
        body: { item_ids: items.map((item) => item.id), numbering: "source", dry_run: false },
      });
      invalidate();
      toast.success(
        `Queued ${result.jobs?.length ?? result.resolved.length} Episodes for archiving`,
        {
          description: result.already_archived_count
            ? `${result.already_archived_count} already archived`
            : undefined,
        },
      );
      return;
    }
    const outcomes = await Promise.allSettled(
      items.map((item) =>
        archive.mutateAsync({ params: { path: { feed_id: feedId, item_id: item.id } } }),
      ),
    );
    invalidate();
    const ok = outcomes.filter((o) => o.status === "fulfilled").length;
    const failed = outcomes.length - ok;
    if (ok) toast.success(`Queued ${ok} Episodes for archiving`);
    if (failed) toast.error(`${failed} could not be queued`);
  };

  const deleteMany = async (items: ItemRead[]) => {
    if (items.length === 0) return;
    const outcomes = await Promise.allSettled(
      items.map((item) =>
        remove.mutateAsync({ params: { path: { feed_id: feedId, item_id: item.id } } }),
      ),
    );
    invalidate();
    const ok = outcomes.filter((o) => o.status === "fulfilled").length;
    const failed = outcomes.length - ok;
    if (ok)
      toast.success(`Deleted ${ok} Episodes`, {
        description: "They stay Available and will not re-archive on their own.",
      });
    if (failed) toast.error(`${failed} could not be deleted`);
  };

  return {
    archiveMany,
    deleteMany,
    isPending: select.isPending || archive.isPending || remove.isPending,
  };
}
