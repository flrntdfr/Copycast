import { createFileRoute, redirect } from "@tanstack/react-router";

// Local feeds were called Inboxes before ADR 0018: old links still land on their page.
export const Route = createFileRoute("/inboxes/$inboxId")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({
      to: "/feed/$feedId",
      params: { feedId: params.inboxId },
      replace: true,
    });
  },
});
