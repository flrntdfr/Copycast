import { createFileRoute, redirect } from "@tanstack/react-router";
import { z } from "zod";

// Buckets were called Inboxes before ADR 0018: old links still land on them.
export const Route = createFileRoute("/inboxes/")({
  validateSearch: z.object({ url: z.string().optional() }),
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({ to: "/", search: { url: search.url }, hash: "buckets", replace: true });
  },
});
