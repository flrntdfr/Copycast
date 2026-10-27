/** Mutations of the Settings page: cookie files, the Mirror defaults, clearing a bucket. */
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { count } from "@/lib/labels";
import { formatMegabytes } from "@/lib/format";

export function useInvalidateCookies() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({
      queryKey: [OPS.get_engine_cookies[0], OPS.get_engine_cookies[1]],
    });
}

export function useSetCookies() {
  const invalidate = useInvalidateCookies();
  return $api.useMutation("put", "/api/engine/cookies", {
    onSuccess: (stored) => {
      void invalidate();
      // The file just written is the newest one.
      const file = [...(stored.files ?? [])].sort((a, b) =>
        (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
      )[0];
      const unlocked = (stored.services ?? []).filter((service) =>
        file?.services?.includes(service.id),
      );
      toast.success("Cookie file stored", {
        description: unlocked.length
          ? `Unlocks ${unlocked.map((service) => service.name).join(", ")}. Retry the failed Episodes.`
          : "It covers none of the listed services; the engine still sends it everywhere.",
      });
    },
  });
}

export function useDeleteCookies() {
  const invalidate = useInvalidateCookies();
  return $api.useMutation("delete", "/api/engine/cookies", {
    onSuccess: () => {
      void invalidate();
      toast.success("Cookie file removed");
    },
  });
}

/** Saves the defaults as they change: no toast, the form shows its own "Saved". */
export function useSetDefaults() {
  const queryClient = useQueryClient();
  return $api.useMutation("put", "/api/settings/defaults", {
    onSuccess: (stored) => {
      queryClient.setQueryData(
        [OPS.get_mirror_defaults[0], OPS.get_mirror_defaults[1], {}],
        stored,
      );
    },
  });
}

/** Deletes what a Feed does not keep for good; the dry run counts it first. */
export function useClearFeed() {
  const invalidate = useInvalidateFeed();
  return $api.useMutation("post", "/api/feeds/{feed_id}/clear", {
    onSuccess: (result, { params, body }) => {
      if (body.dry_run) return;
      invalidate(params.path.feed_id);
      toast.success(`${count(result.deleted_count, "Episode")} deleted`, {
        description: `${formatMegabytes(result.bytes_freed)} freed; ${count(result.kept_count ?? 0, "Episode")} flagged Archive kept.`,
      });
    },
  });
}
