import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, type RouterHistory } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { createAppQueryClient, setUnauthorizedHandler } from "@/api/client";
import { endSession } from "@/features/session/lifecycle";
import { LOGIN_PATH, nextFor } from "@/features/session/next";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { applyTheme, getThemePreference } from "@/lib/theme";
import { createAppRouter } from "./router";

export interface AppProps {
  history?: RouterHistory;
  /** Tests inject a client and skip the EventSource (the shell opens it once signed in). */
  queryClient?: ReturnType<typeof createAppQueryClient>;
  events?: boolean;
}

export function App({ history, queryClient: injected, events = true }: AppProps) {
  const queryClient = useMemo(() => injected ?? createAppQueryClient(), [injected]);
  const router = useMemo(
    () => createAppRouter(queryClient, history, { events }),
    [queryClient, history, events],
  );

  useEffect(() => {
    applyTheme(getThemePreference());
  }, []);

  // One handler for every 401 from `/api`: the session is over, so is everything shown with it.
  useEffect(
    () =>
      setUnauthorizedHandler(() => {
        const location = router.latestLocation;
        if (location.pathname === LOGIN_PATH) return;
        endSession(queryClient);
        void router.navigate({
          to: LOGIN_PATH,
          search: { next: nextFor(location.href) },
          replace: true,
        });
      }),
    [queryClient, router],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <RouterProvider router={router} />
        {/* Toasts sit above the player; sonner's defaults are 24 px (16 px on phones). */}
        <Toaster
          position="bottom-right"
          closeButton
          richColors={false}
          toastOptions={{ duration: 5000 }}
          offset={{ bottom: "calc(var(--player-h, 0px) + 24px)" }}
          mobileOffset={{ bottom: "calc(var(--player-h, 0px) + 16px)" }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
