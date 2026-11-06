import { createFileRoute, redirect } from "@tanstack/react-router";
import { z } from "zod";

import { meQueryOptions } from "@/api/session";
import { LoginPage } from "@/features/session/LoginPage";
import { safeNext } from "@/features/session/next";

const searchSchema = z.object({
  next: z.string().optional().catch(undefined),
});

/**
 * The sign-in page, outside the shell. With authentication off (open mode) or a session
 * already, there is nothing to sign in to: straight on to `next`.
 */
export const Route = createFileRoute("/login")({
  validateSearch: searchSchema,
  beforeLoad: async ({ context, search }) => {
    let signedIn = false;
    try {
      await context.queryClient.ensureQueryData(meQueryOptions());
      signedIn = true;
    } catch {
      // No session (401), or Copycast unreachable: the form explains when it is used.
    }
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    if (signedIn) throw redirect({ href: safeNext(search.next), replace: true });
  },
  component: LoginRoute,
});

function LoginRoute() {
  const { next } = Route.useSearch();
  return <LoginPage next={next} />;
}
