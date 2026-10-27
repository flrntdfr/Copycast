import { createFileRoute, redirect } from "@tanstack/react-router";

// Buckets were called Inboxes before ADR 0018: old links still land on them.
export const Route = createFileRoute("/inboxes/$inboxId")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({
      to: "/buckets/$bucketId",
      params: { bucketId: params.inboxId },
      replace: true,
    });
  },
});
