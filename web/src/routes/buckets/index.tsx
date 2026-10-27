import { createFileRoute, redirect } from "@tanstack/react-router";
import { z } from "zod";

// The Buckets are listed on the Feeds page, under the Mirrors.
export const Route = createFileRoute("/buckets/")({
  validateSearch: z.object({ url: z.string().optional() }),
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({ to: "/", search: { url: search.url }, hash: "buckets", replace: true });
  },
});
