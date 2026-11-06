import { createFileRoute, redirect } from "@tanstack/react-router";
import { useEffect } from "react";

import { ApiProblem } from "@/api/client";
import { meQueryOptions, useMe } from "@/api/session";
import { AppShell } from "@/components/app-shell/AppShell";
import { applySessionUser, startSessionEvents } from "@/features/session/lifecycle";
import { nextFor } from "@/features/session/next";

/**
 * Every page but the sign-in page: the shell, once `/api/me` answered. Without a session the
 * layout sends the browser to `/login?next=…`; with one, the stores switch to that User's keys
 * before any page reads them, and the event stream runs for as long as the User stays.
 */
export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ context, location }) => {
    try {
      const me = await context.queryClient.ensureQueryData(meQueryOptions());
      applySessionUser(me.username);
    } catch (error) {
      if (ApiProblem.is(error) && error.status === 401)
        // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
        throw redirect({ to: "/login", search: { next: nextFor(location.href) }, replace: true });
      throw error;
    }
  },
  component: AppLayout,
});

function AppLayout() {
  const { queryClient, events } = Route.useRouteContext();
  const username = useMe()?.username;

  // `/api/me` re-read as someone else (a sign-in in another tab): their keys, their stream.
  useEffect(() => {
    if (username) applySessionUser(username);
  }, [username]);

  useEffect(() => {
    if (!events || !username) return undefined;
    return startSessionEvents(queryClient, username);
  }, [events, queryClient, username]);

  return <AppShell />;
}
