import { createRootRouteWithContext } from "@tanstack/react-router";

import { AppShell } from "@/components/app-shell/AppShell";
import type { RouterContext } from "@/router";

export const Route = createRootRouteWithContext<RouterContext>()({
  component: AppShell,
});
