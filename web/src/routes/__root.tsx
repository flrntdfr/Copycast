import { Outlet, createRootRouteWithContext } from "@tanstack/react-router";

import type { RouterContext } from "@/router";

/** Two children: the sign-in page, and the `_app` layout (the shell) around everything else. */
export const Route = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
});
