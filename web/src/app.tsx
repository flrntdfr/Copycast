import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, type RouterHistory } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { createAppQueryClient } from "@/api/client";
import { startEventStream } from "@/api/events";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { applyTheme, getThemePreference } from "@/lib/theme";
import { createAppRouter } from "./router";

export interface AppProps {
  history?: RouterHistory;
  /** Tests inject a client and skip the EventSource. */
  queryClient?: ReturnType<typeof createAppQueryClient>;
  events?: boolean;
}

export function App({ history, queryClient: injected, events = true }: AppProps) {
  const queryClient = useMemo(() => injected ?? createAppQueryClient(), [injected]);
  const router = useMemo(() => createAppRouter(queryClient, history), [queryClient, history]);

  useEffect(() => {
    applyTheme(getThemePreference());
  }, []);

  useEffect(() => {
    if (!events) return undefined;
    return startEventStream(queryClient);
  }, [events, queryClient]);

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <RouterProvider router={router} />
        <Toaster
          position="bottom-right"
          closeButton
          richColors={false}
          toastOptions={{ duration: 5000 }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
