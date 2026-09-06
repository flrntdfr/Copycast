/**
 * The one HTTP client. Every error surfaces as an ApiProblem (problem+json parsed,
 * network failures wrapped); query keys are `[method, path, init]` from openapi-react-query.
 */
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import createFetchClient, { type Middleware } from "openapi-fetch";
import createQueryClient from "openapi-react-query";
import { toast } from "sonner";

import { ApiProblem, describeProblem, problemFromBody } from "./problem";
import type { paths } from "./schema";

export { ApiProblem, asError, describeProblem } from "./problem";

const problemMiddleware: Middleware = {
  async onResponse({ request, response }) {
    if (response.ok) return undefined;
    let body: unknown = null;
    const text = await response.clone().text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    throw problemFromBody(body, response, request);
  },
  onError({ error, request }) {
    if (ApiProblem.is(error)) return error;
    if (error instanceof DOMException && error.name === "AbortError") {
      return new ApiProblem({
        slug: "aborted",
        status: 0,
        title: "Cancelled",
        method: request.method,
        url: request.url,
        cause: error,
      });
    }
    return new ApiProblem({
      slug: "network",
      status: 0,
      title: "Network error",
      detail: error instanceof Error ? error.message : String(error),
      method: request.method,
      url: request.url,
      cause: error,
    });
  },
};

/** Same origin in the browser; an absolute origin is what Node's fetch (tests) requires. */
export const API_BASE_URL =
  typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";

export const fetchClient = createFetchClient<paths>({
  baseUrl: API_BASE_URL,
  // Late-bound so request interceptors installed after this module loads (MSW in tests) apply.
  fetch: (request) => globalThis.fetch(request),
  headers: { Accept: "application/json, application/problem+json" },
});
fetchClient.use(problemMiddleware);

/** Typed hooks: `$api.useQuery("get", "/api/feeds", { params: { query: {...} } })`. */
export const $api = createQueryClient(fetchClient);

/** Errors the UI handles inline and must not toast globally. */
const SILENT_SLUGS = new Set([
  "aborted",
  "feed-exists",
  "candidates-ambiguous",
  "invalid-selection",
]);

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: (failureCount, error) => {
          if (ApiProblem.is(error) && error.status > 0 && error.status < 500) return false;
          return failureCount < 2;
        },
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
    queryCache: new QueryCache({
      onError: (error, query) => {
        // Only surface background failures for data we already showed once.
        if (query.state.data === undefined) return;
        if (ApiProblem.is(error) && error.slug === "aborted") return;
        const { title, description } = describeProblem(error);
        toast.error(title, { description, id: `query:${title}` });
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silent === true) return;
        if (ApiProblem.is(error) && SILENT_SLUGS.has(error.slug)) return;
        const { title, description } = describeProblem(error);
        toast.error(title, { description });
      },
    }),
  });
}

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: ApiProblem;
    mutationMeta: { silent?: boolean };
  }
}
