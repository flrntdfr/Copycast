import { createFileRoute, redirect } from "@tanstack/react-router";

import { feedSearchSchema } from "@/features/feeds/search";

// A Mirror's page is its feed page now: old links keep working, search params included.
export const Route = createFileRoute("/mirrors/$mirrorId")({
  validateSearch: feedSearchSchema,
  beforeLoad: ({ params, search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({
      to: "/feed/$feedId",
      params: { feedId: params.mirrorId },
      search,
      replace: true,
    });
  },
});
