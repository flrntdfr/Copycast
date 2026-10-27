import { createFileRoute, redirect } from "@tanstack/react-router";

// The Mirrors are listed on the Feeds page.
export const Route = createFileRoute("/mirrors/")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({ to: "/", replace: true });
  },
});
