/** Mutations of the API keys page: mint and revoke, then refetch the list. */
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";

export function useInvalidateKeys() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: [OPS.list_api_keys[0], OPS.list_api_keys[1]] });
}

export function useCreateApiKey() {
  const invalidate = useInvalidateKeys();
  return $api.useMutation("post", "/api/keys", {
    onSuccess: () => void invalidate(),
  });
}

export function useRevokeApiKey() {
  const invalidate = useInvalidateKeys();
  return $api.useMutation("delete", "/api/keys/{key_id}", {
    onSuccess: () => {
      void invalidate();
      toast.success("API key revoked");
    },
  });
}
