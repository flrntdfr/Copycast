/** Render helpers: providers only, or the whole App at a memory-history location. */
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, type RouterHistory } from "@tanstack/react-router";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

import { App } from "@/app";
import { createAppQueryClient } from "@/api/client";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

export function testQueryClient(): QueryClient {
  const client = createAppQueryClient();
  client.setDefaultOptions({
    queries: { retry: false, staleTime: 0, gcTime: 0 },
    mutations: { retry: false },
  });
  return client;
}

export function Providers({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={0}>
        {children}
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export function renderWithProviders(
  ui: ReactElement,
  options: Omit<RenderOptions, "wrapper"> & { client?: QueryClient } = {},
): RenderResult & { client: QueryClient } {
  const client = options.client ?? testQueryClient();
  const result = render(ui, {
    ...options,
    wrapper: ({ children }) => <Providers client={client}>{children}</Providers>,
  });
  return { ...result, client };
}

/** Mount the full App (routes, shell, toasts) at `path`; the event stream stays off. */
export function renderApp(
  path: string,
  client = testQueryClient(),
): RenderResult & { client: QueryClient; history: RouterHistory } {
  const history = createMemoryHistory({ initialEntries: [path] });
  const result = render(<App history={history} queryClient={client} events={false} />);
  return { ...result, client, history };
}
