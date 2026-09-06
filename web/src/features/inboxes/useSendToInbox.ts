import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { isInbox, type InboxRead } from "@/api/types";

/** The Inbox named "Copycast" when it exists (the default one unless renamed), else the first. */
export function pickDefaultInbox(inboxes: readonly InboxRead[]): InboxRead | null {
  return inboxes.find((inbox) => inbox.name.toLowerCase() === "copycast") ?? inboxes[0] ?? null;
}

/** Push a URL into an Inbox as a Request and open that Inbox. */
export function useSendToInbox() {
  const navigate = useNavigate();
  const inboxes = $api.useQuery("get", "/api/feeds", { params: { query: { kind: "inbox" } } });
  const mutation = $api.useMutation("post", "/api/inboxes/{inbox_id}/requests", {
    onSuccess: (request) => {
      toast.success("Sent to the Inbox", { description: request.url });
      void navigate({
        to: "/inboxes/$inboxId",
        params: { inboxId: request.inbox_id },
        search: { tab: "requests" },
      });
    },
  });
  const candidates = (inboxes.data?.feeds ?? []).filter(isInbox);
  const target = pickDefaultInbox(candidates);
  return {
    inboxes: candidates,
    target,
    isPending: mutation.isPending,
    send: (url: string, inboxId = target?.id) => {
      if (!inboxId) {
        toast.error("No Inbox is available yet");
        return Promise.resolve(null);
      }
      return mutation.mutateAsync({ params: { path: { inbox_id: inboxId } }, body: { url } });
    },
  };
}
