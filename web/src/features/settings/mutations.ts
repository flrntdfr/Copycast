/** Mutations of the Settings page: store and remove the engine's cookie file. */
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";

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
      toast.success("Cookies stored", {
        description: stored.youtube
          ? `${stored.cookie_count} cookies, including youtube.com. Retry the failed Episodes.`
          : `${stored.cookie_count} cookies, none for youtube.com.`,
      });
    },
  });
}

export function useDeleteCookies() {
  const invalidate = useInvalidateCookies();
  return $api.useMutation("delete", "/api/engine/cookies", {
    onSuccess: () => {
      void invalidate();
      toast.success("Cookies removed", { description: "Fetches are anonymous again." });
    },
  });
}

export function useSetDefaults() {
  const queryClient = useQueryClient();
  return $api.useMutation("put", "/api/settings/defaults", {
    onSuccess: (stored) => {
      queryClient.setQueryData(
        [OPS.get_mirror_defaults[0], OPS.get_mirror_defaults[1], {}],
        stored,
      );
      void queryClient.invalidateQueries({
        queryKey: [OPS.get_mirror_defaults[0], OPS.get_mirror_defaults[1]],
      });
      toast.success("Defaults saved", {
        description: "Mirrors without their own value follow them from the next Refresh.",
      });
    },
  });
}
