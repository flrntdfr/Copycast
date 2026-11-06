import type { QueryClient } from "@tanstack/react-query";
import { createRouter, type RouterHistory } from "@tanstack/react-router";

import { routeTree } from "./routeTree.gen";
import { RouteError } from "@/components/common/RouteError";
import { NotFound } from "@/components/common/NotFound";
import { PageSpinner } from "@/components/common/PageSpinner";

export interface RouterContext {
  queryClient: QueryClient;
  /** Whether the shell opens the event stream (tests turn it off). */
  events: boolean;
}

export function createAppRouter(
  queryClient: QueryClient,
  history?: RouterHistory,
  { events = true }: { events?: boolean } = {},
) {
  return createRouter({
    routeTree,
    context: { queryClient, events },
    history,
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
    defaultErrorComponent: RouteError,
    defaultNotFoundComponent: NotFound,
    defaultPendingComponent: PageSpinner,
    defaultPendingMs: 300,
    scrollRestoration: true,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
