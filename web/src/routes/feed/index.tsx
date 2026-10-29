import { createFileRoute, redirect } from "@tanstack/react-router";

// Every Feed is listed on the Feeds page.
export const Route = createFileRoute("/feed/")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router's redirect is thrown by design
    throw redirect({ to: "/", replace: true });
  },
});
